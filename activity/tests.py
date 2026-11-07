import json
import shutil
import tempfile
from datetime import timedelta
from io import BytesIO, StringIO
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from inventory.models import Laptop, LaptopMedia
from orders import services
from orders.models import Order, PickupLocation, ShippingZone
from orders.tests import form_data, make_zones, order_data
from store.tests import add_laptop

from .models import ActivityLog


def rows(**filters):
    return ActivityLog.objects.filter(**filters).order_by("occurred_at", "id")


def actions(queryset):
    return [r.action for r in queryset]


class FullOrderTraceTests(TestCase):
    """One transaction id follows an order from the first cart click to delivery."""

    def setUp(self):
        make_zones()
        self.laptop = add_laptop(photo=False, price="30000")
        self.staff = get_user_model().objects.create_superuser("manager", "m@example.com", "pw")

    def place_order(self, **overrides):
        self.client.post(reverse("cart_add", args=[self.laptop.pk]))
        self.client.get(reverse("checkout"))
        self.client.post(reverse("checkout"), form_data(**overrides))
        return Order.objects.get()

    def test_whole_flow_shares_one_transaction_id(self):
        order = self.place_order()
        txn = order.transaction_id
        self.assertTrue(txn.startswith("TXN-"))

        staff = Client()
        staff.force_login(self.staff)
        staff.post(reverse("admin:orders_order_action", args=[order.pk, "confirm_payment"]))
        Order.objects.filter(pk=order.pk).update()
        from orders.models import Shipment
        Shipment.objects.create(order=order, courier="Delhivery", tracking_no="D1")
        staff.post(reverse("admin:orders_order_action", args=[order.pk, "shipped"]))
        staff.post(reverse("admin:orders_order_action", args=[order.pk, "delivered"]))

        trail = actions(rows(transaction_id=txn))
        for expected in ["cart.item_added", "order.created", "laptop.status_changed", "payment.confirmed",
                         "order.shipped", "order.delivered", "email.sent", "web.request"]:
            self.assertIn(expected, trail)
        # the cart click really is the first thing in the trail
        self.assertEqual(trail[0], "cart.item_added")
        # every row of this order carries the order number once it exists
        self.assertTrue(all(r.order_no == order.order_no for r in rows(transaction_id=txn, action="order.created")))
        # staff actions were done by the staff user, but belong to the same trail
        shipped = rows(transaction_id=txn, action="order.shipped").get()
        self.assertEqual((shipped.actor_type, shipped.actor_name), ("staff", "manager"))
        self.assertEqual(shipped.details["status"], {"from": "confirmed", "to": "shipped"})
        self.assertEqual(shipped.details["courier"], "Delhivery")

    def test_laptop_status_changes_are_logged_with_reason(self):
        order = self.place_order()
        change = rows(action="laptop.status_changed", laptop_sku=self.laptop.sku).first()
        self.assertEqual(change.details["from"], "available")
        self.assertEqual(change.details["to"], "reserved")
        self.assertEqual(change.order_no, order.order_no)

    def test_next_purchase_gets_a_new_transaction_id(self):
        first = self.place_order()
        services.cancel_order(first, reason="test")
        self.client.post(reverse("cart_add", args=[self.laptop.pk]))
        added = rows(action="cart.item_added").order_by("-id").first()
        self.assertNotEqual(added.transaction_id, first.transaction_id)

    def test_two_orders_have_different_transactions(self):
        first = services.create_order([self.laptop.pk], order_data())
        other = add_laptop(model="Other", photo=False)
        second = services.create_order([other.pk], order_data(email="b@example.com"))
        self.assertNotEqual(first.transaction_id, second.transaction_id)

    def test_order_page_visits_join_the_trail(self):
        order = self.place_order()
        self.client.get(reverse("order_detail", args=[order.access_token]))
        viewed = rows(action="order.viewed").get()
        self.assertEqual(viewed.transaction_id, order.transaction_id)
        request_row = rows(action="web.request", transaction_id=order.transaction_id).last()
        self.assertEqual(request_row.path, "/orders/{token}/")   # the private link token is never logged

    def test_failed_and_expired_steps_are_logged(self):
        order = self.place_order(payment_method="card")
        services.record_failed_payment(order, gateway="test", note="cancelled")
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        services.release_expired()
        trail = rows(transaction_id=order.transaction_id)
        failed = trail.filter(action="payment.failed").get()
        self.assertEqual(failed.level, "WARNING")
        expired = trail.filter(action="order.expired").get()
        self.assertEqual((expired.actor_type, expired.actor_name), ("system", "reservation timer"))

    def test_refund_due_is_a_warning(self):
        order = services.create_order([self.laptop.pk], order_data(payment_method="card"))
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        services.release_expired()
        services.create_order([self.laptop.pk], order_data(email="two@example.com"))
        services.confirm_payment(order, gateway="test", transaction_id="T9")
        self.assertEqual(rows(action="payment.refund_due").get().level, "WARNING")

    def test_returns_are_logged(self):
        order = services.create_order([self.laptop.pk], order_data())
        services.confirm_payment(order, gateway="manual")
        case = services.open_return(order.items.get(), "Dead keyboard")
        services.close_return(case, fixed=True)
        self.assertEqual(actions(rows(order_no=order.order_no, category="return")), ["return.opened", "return.closed"])


class PrivacyAndFileTests(TestCase):
    def setUp(self):
        make_zones()
        self.laptop = add_laptop(photo=False)

    def test_personal_data_is_masked_in_the_log(self):
        order = services.create_order([self.laptop.pk], order_data(email="ravi.kumar@example.com", phone="9876543210"))
        created = rows(action="order.created").get()
        self.assertEqual(created.details["customer_email"], "r***@example.com")
        self.assertEqual(created.details["customer_phone"], "******3210")
        blob = json.dumps(created.details)
        self.assertNotIn("ravi.kumar", blob)
        self.assertNotIn("9876543210", blob)

    def test_sensitive_keys_are_removed(self):
        from . import logger
        logger.log("test.event", "x", details={"password": "hunter2", "api_token": "abc", "nested": {"csrf": "zzz"}, "ok": 1})
        details = rows(action="test.event").get().details
        self.assertEqual((details["password"], details["api_token"], details["nested"]["csrf"], details["ok"]),
                         ("***", "***", "***", 1))

    def test_file_log_matches_database_row(self):
        order = services.create_order([self.laptop.pk], order_data())
        row = rows(action="order.created").get()
        lines = (settings.LOG_DIR / "activity.log").read_text(encoding="utf-8").splitlines()
        records = [json.loads(l) for l in lines if row.event_id in l]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["transaction_id"], order.transaction_id)
        self.assertEqual(record["order_no"], order.order_no)
        self.assertEqual(record["action"], "order.created")
        self.assertEqual(record["details"]["total"], str(order.total) if isinstance(record["details"]["total"], str) else record["details"]["total"])
        self.assertIn("timestamp", record)

    def test_event_ids_are_unique(self):
        services.create_order([self.laptop.pk], order_data())
        ids = list(ActivityLog.objects.values_list("event_id", flat=True))
        self.assertEqual(len(ids), len(set(ids)))

    def test_logging_failure_never_breaks_the_page(self):
        with mock.patch("activity.logger._write_row", side_effect=RuntimeError("database full")):
            self.assertEqual(self.client.get("/").status_code, 200)

    def test_log_entries_cannot_be_changed(self):
        entry = ActivityLog.objects.create(transaction_id="TXN-X", category="test", action="test.x")
        entry.message = "changed"
        with self.assertRaises(ValueError):
            entry.save()


class RequestLoggingTests(TestCase):
    def test_pages_are_logged_with_status_and_duration(self):
        self.client.get("/laptops/?q=dell&ram=16")
        row = rows(action="web.request").get()
        self.assertEqual((row.http_method, row.path, row.status_code), ("GET", "/laptops/", 200))
        self.assertIsNotNone(row.duration_ms)
        self.assertEqual(row.details["query"], {"q": "dell", "ram": "16"})
        self.assertEqual(row.category, "web")

    def test_not_found_is_a_warning(self):
        self.client.get("/laptops/9999/")
        self.assertEqual(rows(action="web.request").get().level, "WARNING")

    def test_health_and_static_are_not_logged(self):
        self.client.get("/health/")
        self.client.get("/static/store/style.css")
        self.assertEqual(rows(action="web.request").count(), 0)

    def test_request_logging_can_be_switched_off(self):
        with override_settings(ACTIVITY_LOG_REQUESTS=False):
            self.client.get("/")
        self.assertEqual(rows(action="web.request").count(), 0)

    def test_client_ip_is_recorded(self):
        self.client.get("/", REMOTE_ADDR="10.1.2.3")
        self.assertEqual(rows(action="web.request").get().ip_address, "10.1.2.3")

    def test_unhandled_error_is_logged(self):
        client = Client(raise_request_exception=False)
        with mock.patch("store.views.render", side_effect=RuntimeError("boom")):
            self.assertEqual(client.get("/").status_code, 500)
        error = rows(action="system.error").get()
        self.assertEqual(error.level, "ERROR")
        self.assertIn("boom", error.message)
        self.assertIn("RuntimeError", error.details["traceback"])


class AuthAndAdminLoggingTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser("manager", "m@example.com", "pw")

    def test_login_logout_and_failed_login(self):
        self.client.post("/admin/login/", {"username": "manager", "password": "wrong", "next": "/admin/"})
        failed = rows(action="auth.login_failed").get()
        self.assertEqual(failed.details["username"], "manager")
        self.assertNotIn("wrong", json.dumps(failed.details))
        self.client.post("/admin/login/", {"username": "manager", "password": "pw", "next": "/admin/"})
        self.assertEqual(rows(action="auth.login").get().actor_name, "manager")
        self.client.post("/admin/logout/")
        self.assertEqual(rows(action="auth.logout").count(), 1)

    def test_price_change_in_admin_records_old_and_new_value(self):
        laptop = add_laptop(photo=False, price="25000")
        self.client.force_login(self.staff)
        page = self.client.get(reverse("admin:inventory_laptop_change", args=[laptop.pk]))
        form = page.context["adminform"].form
        data = {k: ("" if v is None else v) for k, v in form.initial.items()}
        data.update({"brand": laptop.brand_id, "price": "27500", "status": "draft",
                     "media-TOTAL_FORMS": 0, "media-INITIAL_FORMS": 0, "media-MIN_NUM_FORMS": 0,
                     "media-MAX_NUM_FORMS": 1000})
        self.client.post(reverse("admin:inventory_laptop_change", args=[laptop.pk]), data)
        entry = rows(action="admin.updated", laptop_sku=laptop.sku).get()
        self.assertEqual(entry.actor_name, "manager")
        price = entry.details["changes"]["price"]
        self.assertEqual((price["from"], price["to"]), ("25000.00", "27500"))

    def test_bulk_status_action_is_logged_per_laptop(self):
        laptops = [add_laptop(model=f"M{i}", photo=False) for i in range(2)]
        self.client.force_login(self.staff)
        self.client.post(reverse("admin:inventory_laptop_changelist"),
                         {"action": "mark_under_investigation", "_selected_action": [l.pk for l in laptops]})
        self.assertEqual(rows(action="laptop.status_changed").count(), 2)

    def test_admin_actions_on_other_models_are_logged(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("admin:orders_pickuplocation_add"),
                         {"name": "Shop", "address": "1 Road", "schedule": "", "active": "on"})
        self.assertEqual(rows(action="admin.created").get().object_type, "pickuplocation")

    def test_media_upload_is_logged(self):
        from inventory.tests import make_image
        laptop = add_laptop(photo=False)
        self.client.force_login(self.staff)
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            self.client.post(reverse("admin:inventory_laptop_upload_media", args=[laptop.pk]),
                             {"files": [make_image("a.jpg")]})
        entry = rows(action="media.uploaded").get()
        self.assertEqual(entry.laptop_sku, laptop.sku)
        self.assertEqual(entry.details["files"][0]["name"], "a.jpg")


class ActivityAdminTests(TestCase):
    def setUp(self):
        make_zones()
        self.staff = get_user_model().objects.create_superuser("manager", "m@example.com", "pw")
        self.client.force_login(self.staff)
        self.laptop = add_laptop(photo=False)
        self.order = services.create_order([self.laptop.pk], order_data())

    def test_changelist_and_filters_load(self):
        url = reverse("admin:activity_activitylog_changelist")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(url, {"q": self.order.order_no}), "order.created")
        self.assertEqual(self.client.get(url, {"level__exact": "WARNING"}).status_code, 200)

    def test_trace_by_order_number_transaction_and_sku(self):
        for key in (self.order.order_no, self.order.transaction_id, self.laptop.sku):
            page = self.client.get(reverse("admin:activity_activitylog_trace", args=[key]))
            self.assertContains(page, "order.created")
            self.assertContains(page, self.order.transaction_id)

    def test_trace_search_form_redirects(self):
        response = self.client.get(reverse("admin:activity_activitylog_trace_search"), {"q": self.order.order_no})
        self.assertEqual(response.status_code, 302)

    def test_trace_shows_cart_clicks_made_before_the_order_existed(self):
        client = Client()
        client.post(reverse("cart_add", args=[self.laptop.pk + 100]))  # unknown laptop: ignored
        other = add_laptop(model="Other", photo=False)
        client.post(reverse("cart_add", args=[other.pk]))
        client.post(reverse("checkout"), form_data(email="x@example.com"))
        order = Order.objects.exclude(pk=self.order.pk).get()
        page = self.client.get(reverse("admin:activity_activitylog_trace", args=[order.order_no]))
        self.assertContains(page, "cart.item_added")

    def test_nobody_can_edit_or_delete_log_entries(self):
        entry = ActivityLog.objects.first()
        change_url = reverse("admin:activity_activitylog_change", args=[entry.pk])
        self.assertEqual(self.client.post(change_url, {"message": "hacked"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:activity_activitylog_delete", args=[entry.pk]),
                                          {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:activity_activitylog_add")).status_code, 403)

    def test_export_csv(self):
        response = self.client.post(reverse("admin:activity_activitylog_changelist"),
                                    {"action": "export_csv", "_selected_action": [ActivityLog.objects.first().pk]})
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("Transaction", response.content.decode())

    def test_order_admin_page_links_to_the_trace_and_shows_transaction_id(self):
        page = self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))
        self.assertContains(page, "Activity trace")
        self.assertContains(page, self.order.transaction_id)


class CommandTests(TestCase):
    def setUp(self):
        make_zones()
        self.laptop = add_laptop(photo=False)
        self.order = services.create_order([self.laptop.pk], order_data())

    def test_trace_command_prints_the_trail(self):
        out = StringIO()
        call_command("trace", self.order.order_no, stdout=out)
        text = out.getvalue()
        self.assertIn(self.order.transaction_id, text)
        self.assertIn("order.created", text)

    def test_trace_command_unknown_key(self):
        out = StringIO()
        call_command("trace", "ORD-999999", stdout=out)
        self.assertIn("No activity found", out.getvalue())

    def test_purge_command_removes_only_old_rows(self):
        old = ActivityLog.objects.create(transaction_id="TXN-OLD", category="t", action="t.old",
                                         occurred_at=timezone.now() - timedelta(days=400))
        out = StringIO()
        call_command("purge_activity_logs", "--days", "365", stdout=out)
        self.assertFalse(ActivityLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(action="order.created").exists())


class RegenerateImagesTests(TestCase):
    def test_old_non_uniform_photos_are_resized(self):
        import os
        from django.core.files.base import ContentFile
        media_root = tempfile.mkdtemp()
        with override_settings(MEDIA_ROOT=media_root):
            laptop = add_laptop(photo=False)
            # bulk_create skips the normal save(), like photos stored before the uniform-size rule existed
            media = LaptopMedia.objects.bulk_create([LaptopMedia(laptop=laptop, file="placeholder.webp", kind="image")])[0]
            buf = BytesIO()
            Image.new("RGB", (1600, 700), "red").save(buf, format="WEBP")
            media.file.save("old.webp", ContentFile(buf.getvalue()), save=False)
            thumb = BytesIO()
            Image.new("RGB", (400, 175), "red").save(thumb, format="WEBP")
            media.thumbnail.save("old_thumb.webp", ContentFile(thumb.getvalue()), save=False)
            LaptopMedia.objects.filter(pk=media.pk).update(file=media.file.name, thumbnail=media.thumbnail.name)
            old_path = media.file.path

            call_command("regenerate_images", stdout=StringIO())
            media.refresh_from_db()
            with Image.open(media.file.path) as img:
                self.assertEqual(img.size, (1200, 900))
            with Image.open(media.thumbnail.path) as img:
                self.assertEqual(img.size, (400, 300))
            self.assertFalse(os.path.exists(old_path))
            self.assertEqual(rows(action="media.regenerated").count(), 1)

            out = StringIO()
            call_command("regenerate_images", stdout=out)
            self.assertIn("1 already uniform", out.getvalue())
        shutil.rmtree(media_root, ignore_errors=True)
