from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import Laptop
from store.tests import add_laptop

from . import services
from .models import Order, PickupLocation, ReturnCase, Shipment, ShippingZone
from .services import OrderError


def make_zones():
    ShippingZone.objects.create(name="Rest of India", fee="199", eta="4-6 days", is_default=True)
    ShippingZone.objects.create(name="Kerala", states="Kerala", fee="99", eta="2-3 days")
    return PickupLocation.objects.create(name="Shop", address="1 Main Road", schedule="Mon-Sat 10-6")


def order_data(**kwargs):
    data = dict(customer_name="Ravi Kumar", email="ravi@example.com", phone="9876543210",
                delivery_method="delivery", address_line1="12 MG Road", city="Kochi", state="Kerala",
                pin_code="682001", payment_method="bank_transfer", customer_notes="")
    data.update(kwargs)
    return data


def form_data(**kwargs):
    data = order_data(**kwargs)
    data.setdefault("address_line2", "")
    return data


class ShippingTests(TestCase):
    def test_zone_lookup_and_default(self):
        make_zones()
        self.assertEqual(ShippingZone.for_state("kerala").fee, 99)
        self.assertEqual(ShippingZone.for_state("Punjab").fee, 199)

    def test_no_zone_means_no_delivery(self):
        with self.assertRaises(OrderError):
            services.quote_shipping("delivery", "Kerala")

    def test_pickup_is_free(self):
        location = make_zones()
        zone, loc, fee = services.quote_shipping("pickup", pickup_location_id=location.pk)
        self.assertEqual(fee, 0)
        self.assertEqual(loc, location)


class CartTests(TestCase):
    def test_add_remove_and_unavailable(self):
        laptop = add_laptop(photo=False)
        self.client.post(reverse("cart_add", args=[laptop.pk]))
        self.assertContains(self.client.get(reverse("cart")), "Latitude 7490")
        self.assertEqual(self.client.get(reverse("home")).context["cart_count"], 1)
        self.client.post(reverse("cart_remove", args=[laptop.pk]))
        self.assertContains(self.client.get(reverse("cart")), "Your cart is empty")

    def test_cannot_add_reserved_laptop(self):
        laptop = add_laptop(status="reserved", photo=False)
        self.client.post(reverse("cart_add", args=[laptop.pk]))
        self.assertContains(self.client.get(reverse("cart")), "Your cart is empty")

    def test_laptop_that_becomes_unavailable_is_dropped(self):
        laptop = add_laptop(photo=False)
        self.client.post(reverse("cart_add", args=[laptop.pk]))
        Laptop.objects.filter(pk=laptop.pk).update(status="sold")
        response = self.client.get(reverse("cart"))
        self.assertContains(response, "no longer available")


class OrderServiceTests(TestCase):
    def setUp(self):
        self.location = make_zones()
        self.laptop = add_laptop(photo=False, price="30000")

    def test_create_order_reserves_and_prices(self):
        order = services.create_order([self.laptop.pk], order_data())
        self.laptop.refresh_from_db()
        self.assertEqual(self.laptop.status, "reserved")
        self.assertEqual(order.shipping_fee, Decimal("99"))
        self.assertEqual(order.total, Decimal("30099"))
        self.assertEqual(order.items.get().price, Decimal("30000"))
        self.assertTrue(order.order_no.startswith("ORD-"))
        self.assertEqual(len(mail.outbox), 1)

    def test_same_laptop_cannot_be_ordered_twice(self):
        services.create_order([self.laptop.pk], order_data())
        with self.assertRaises(OrderError):
            services.create_order([self.laptop.pk], order_data(email="other@example.com"))
        self.assertEqual(Order.objects.count(), 1)

    def test_price_change_after_order_does_not_change_order(self):
        order = services.create_order([self.laptop.pk], order_data())
        Laptop.objects.filter(pk=self.laptop.pk).update(price="1")
        self.assertEqual(order.items.get().price, Decimal("30000"))

    def test_expired_order_releases_laptop(self):
        order = services.create_order([self.laptop.pk], order_data())
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(services.release_expired(), 1)
        self.laptop.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.laptop.status, "available")
        self.assertEqual(order.status, "cancelled")

    def test_expired_reservation_lets_next_buyer_order(self):
        order = services.create_order([self.laptop.pk], order_data())
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        second = services.create_order([self.laptop.pk], order_data(email="two@example.com"))
        self.assertEqual(second.status, "pending")

    def test_card_payment_marks_laptop_sold_and_is_idempotent(self):
        order = services.create_order([self.laptop.pk], order_data(payment_method="card"))
        services.confirm_payment(order, gateway="test", transaction_id="T1")
        services.confirm_payment(order, gateway="test", transaction_id="T1")
        order.refresh_from_db()
        self.laptop.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, self.laptop.status), ("confirmed", "paid", "sold"))
        self.assertEqual(order.payments.count(), 1)

    def test_payment_after_expiry_revives_order_if_unit_still_free(self):
        order = services.create_order([self.laptop.pk], order_data(payment_method="card"))
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        services.release_expired()
        services.confirm_payment(order, gateway="test", transaction_id="T2")
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ("confirmed", "paid"))

    def test_payment_after_expiry_when_unit_taken_means_refund_due(self):
        order = services.create_order([self.laptop.pk], order_data(payment_method="card"))
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        services.release_expired()
        services.create_order([self.laptop.pk], order_data(email="two@example.com"))
        services.confirm_payment(order, gateway="test", transaction_id="T3")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "refund_due")
        self.assertEqual(order.status, "cancelled")

    def test_cod_flow_to_delivered_collects_payment(self):
        order = services.create_order([self.laptop.pk], order_data(payment_method="cod"))
        services.confirm_cod(order)
        Shipment.objects.create(order=order, courier="Delhivery", tracking_no="D123")
        services.mark_shipped(Order.objects.get(pk=order.pk))
        services.mark_delivered(Order.objects.get(pk=order.pk))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ("delivered", "paid"))

    def test_shipping_needs_tracking_details(self):
        order = services.create_order([self.laptop.pk], order_data())
        services.confirm_payment(order, gateway="manual")
        with self.assertRaises(OrderError):
            services.mark_shipped(Order.objects.get(pk=order.pk))

    def test_pickup_order_needs_no_courier(self):
        order = services.create_order([self.laptop.pk], order_data(
            delivery_method="pickup", pickup_location_id=self.location.pk, state="", city="", address_line1="",
            pin_code=""))
        self.assertEqual(order.shipping_fee, 0)
        services.confirm_payment(order, gateway="manual")
        services.mark_shipped(Order.objects.get(pk=order.pk))
        self.assertEqual(Order.objects.get(pk=order.pk).status, "shipped")

    def test_cancel_confirmed_paid_order_releases_laptop_and_marks_refund_due(self):
        order = services.create_order([self.laptop.pk], order_data())
        services.confirm_payment(order, gateway="manual")
        services.cancel_order(Order.objects.get(pk=order.pk), reason="Customer asked")
        order.refresh_from_db()
        self.laptop.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, self.laptop.status), ("cancelled", "refund_due", "available"))
        services.mark_refunded(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "refunded")

    def test_return_case_fixed_goes_back_to_available(self):
        order = services.create_order([self.laptop.pk], order_data())
        services.confirm_payment(order, gateway="manual")
        case = services.open_return(order.items.get(), "Keyboard fault")
        self.laptop.refresh_from_db()
        self.assertEqual(self.laptop.status, "under_investigation")
        services.close_return(case, fixed=True)
        self.laptop.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual((self.laptop.status, case.outcome), ("available", ReturnCase.Outcome.FIXED))

    def test_return_case_unfixable_goes_to_supplier(self):
        order = services.create_order([self.laptop.pk], order_data())
        services.confirm_payment(order, gateway="manual")
        case = services.open_return(order.items.get(), "Dead motherboard")
        services.close_return(case, fixed=False)
        self.laptop.refresh_from_db()
        self.assertEqual(self.laptop.status, "returned")
        with self.assertRaises(OrderError):
            services.close_return(case, fixed=True)


class CheckoutFlowTests(TestCase):
    def setUp(self):
        self.location = make_zones()
        self.laptop = add_laptop(photo=False, price="30000")
        self.client.post(reverse("cart_add", args=[self.laptop.pk]))

    def test_checkout_page_and_quote(self):
        self.assertEqual(self.client.get(reverse("checkout")).status_code, 200)
        quote = self.client.get(reverse("checkout_quote"), {"method": "delivery", "state": "Kerala"}).json()
        self.assertTrue(quote["ok"])
        self.assertEqual(quote["fee"], "99.00")

    def test_bank_transfer_order_end_to_end(self):
        response = self.client.post(reverse("checkout"), form_data())
        order = Order.objects.get()
        self.assertRedirects(response, reverse("order_detail", args=[order.access_token]))
        self.assertEqual(self.client.session.get("cart"), [])
        page = self.client.get(reverse("order_detail", args=[order.access_token]))
        self.assertContains(page, order.order_no)
        self.client.post(reverse("order_bank_reference", args=[order.access_token]), {"bank_reference": "UTR12345"})
        order.refresh_from_db()
        self.assertEqual(order.bank_reference, "UTR12345")

    def test_card_order_uses_test_gateway(self):
        response = self.client.post(reverse("checkout"), form_data(payment_method="card"))
        order = Order.objects.get()
        gateway = reverse("test_gateway", args=[order.access_token])
        self.assertRedirects(response, gateway)
        self.client.post(gateway, {"outcome": "fail"})
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.client.post(gateway, {"outcome": "success"})
        order.refresh_from_db()
        self.laptop.refresh_from_db()
        self.assertEqual((order.status, self.laptop.status), ("confirmed", "sold"))

    def test_validation_errors(self):
        response = self.client.post(reverse("checkout"), form_data(phone="12345", pin_code="12"))
        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(response, "valid 10-digit")
        self.assertContains(response, "valid 6-digit")

    def test_delivery_requires_address_and_pickup_requires_location(self):
        self.client.post(reverse("checkout"), form_data(address_line1="", city=""))
        self.assertEqual(Order.objects.count(), 0)
        self.client.post(reverse("checkout"), form_data(delivery_method="pickup"))
        self.assertEqual(Order.objects.count(), 0)

    def test_laptop_taken_by_someone_else_during_checkout(self):
        other = services.create_order([self.laptop.pk], order_data(email="x@example.com"))
        response = self.client.post(reverse("checkout"), form_data())
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(response.status_code, 302)

    def test_track_order(self):
        self.client.post(reverse("checkout"), form_data())
        order = Order.objects.get()
        response = self.client.post(reverse("order_track"), {"order_no": order.order_no, "phone": "+91 98765 43210"})
        self.assertRedirects(response, reverse("order_detail", args=[order.access_token]))
        bad = self.client.post(reverse("order_track"), {"order_no": order.order_no, "phone": "9000000000"})
        self.assertContains(bad, "could not find")

    def test_order_page_unknown_token_is_404(self):
        self.assertEqual(self.client.get(reverse("order_detail", args=["nope"])).status_code, 404)


class OrderAdminTests(TestCase):
    def setUp(self):
        make_zones()
        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(user)
        self.laptop = add_laptop(photo=False)
        self.order = services.create_order([self.laptop.pk], order_data())

    def test_admin_pages_load(self):
        self.assertEqual(self.client.get(reverse("admin:orders_order_changelist")).status_code, 200)
        page = self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Mark payment received")
        self.assertEqual(self.client.get(reverse("admin:orders_order_invoice", args=[self.order.pk])).status_code, 200)

    def test_action_button_confirms_order(self):
        url = reverse("admin:orders_order_action", args=[self.order.pk, "confirm_payment"])
        self.assertEqual(self.client.get(url).status_code, 405)   # buttons must POST
        self.client.post(url)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "confirmed")

    def test_unavailable_action_is_refused(self):
        self.client.post(reverse("admin:orders_order_action", args=[self.order.pk, "delivered"]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")

    def test_bulk_cancel_and_return_case_screens(self):
        self.client.post(reverse("admin:orders_order_changelist"),
                         {"action": "do_cancel", "_selected_action": [self.order.pk]})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")
        self.assertEqual(self.client.get(reverse("admin:orders_returncase_add")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:orders_shippingzone_changelist")).status_code, 200)
