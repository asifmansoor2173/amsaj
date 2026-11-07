"""All business rules for orders live here. Views and admin screens only call these functions."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from inventory.models import Laptop

from . import emails
from .models import Order, OrderItem, Payment, PickupLocation, ReturnCase, Shipment, ShippingZone

Status = Order.Status
PayStatus = Order.PaymentStatus


class OrderError(Exception):
    """Raised with a message that is safe to show to the user."""


# ---- shipping -------------------------------------------------------------

def quote_shipping(delivery_method, state="", pickup_location_id=None):
    """Return (zone, pickup_location, fee) for the chosen delivery option."""
    if delivery_method == Order.Delivery.PICKUP:
        location = PickupLocation.objects.filter(pk=pickup_location_id, active=True).first()
        if not location:
            raise OrderError("Please choose a pickup location.")
        return None, location, 0
    zone = ShippingZone.for_state(state)
    if not zone:
        raise OrderError(f"Sorry, we do not deliver to {state or 'that state'} yet. You can choose pickup instead.")
    return zone, None, zone.fee


# ---- creating and releasing orders ----------------------------------------

def _hold_until(method):
    now = timezone.now()
    if method == Order.Method.CARD:
        return now + timedelta(minutes=settings.RESERVATION_MINUTES_CARD)
    return now + timedelta(hours=settings.RESERVATION_HOURS_OTHER)


def create_order(laptop_ids, data):
    """Reserve the laptops and create the order. Two buyers can never get the same unit:
    the laptop rows are locked while we check their status."""
    release_expired()
    ids = list(dict.fromkeys(laptop_ids))
    if not ids:
        raise OrderError("Your cart is empty.")
    zone, location, fee = quote_shipping(data["delivery_method"], data.get("state", ""),
                                         data.get("pickup_location_id"))
    with transaction.atomic():
        laptops = list(Laptop.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
        if len(laptops) != len(ids):
            raise OrderError("Some laptops in your cart no longer exist.")
        gone = [f"{l.brand} {l.model}" for l in laptops if l.status != Laptop.Status.AVAILABLE]
        if gone:
            raise OrderError("Sorry, " + ", ".join(gone) + " is no longer available. It was removed from your cart.")
        subtotal = sum(l.price for l in laptops)
        order = Order.objects.create(
            customer_name=data["customer_name"], email=data["email"], phone=data["phone"],
            delivery_method=data["delivery_method"],
            address_line1=data.get("address_line1", ""), address_line2=data.get("address_line2", ""),
            city=data.get("city", ""), state=data.get("state", ""), pin_code=data.get("pin_code", ""),
            shipping_zone=zone, pickup_location=location,
            payment_method=data["payment_method"], customer_notes=data.get("customer_notes", ""),
            subtotal=subtotal, shipping_fee=fee, total=subtotal + fee,
            expires_at=_hold_until(data["payment_method"]),
        )
        OrderItem.objects.bulk_create([
            OrderItem(order=order, laptop=l, sku=l.sku, title=l.title, price=l.price) for l in laptops])
        Laptop.objects.filter(pk__in=ids).update(status=Laptop.Status.RESERVED)
    emails.order_placed(order)
    return order


def release_expired(now=None):
    """Cancel unpaid orders whose reservation time has run out and put the laptops back on sale."""
    now = now or timezone.now()
    ids = list(Order.objects.filter(status=Status.PENDING, expires_at__lt=now).values_list("pk", flat=True))
    for pk in ids:
        try:
            cancel_order(Order.objects.get(pk=pk), reason="Reservation expired", notify=False)
        except OrderError:
            pass
    return len(ids)


def cancel_order(order, reason="", notify=True):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status not in (Status.PENDING, Status.CONFIRMED, Status.PROCESSING):
            raise OrderError("This order can no longer be cancelled. Use a return case instead.")
        Laptop.objects.filter(order_items__order=order,
                              status__in=[Laptop.Status.RESERVED, Laptop.Status.SOLD]
                              ).update(status=Laptop.Status.AVAILABLE)
        if order.payment_status == PayStatus.PAID:
            order.payment_status = PayStatus.REFUND_DUE
        order.status = Status.CANCELLED
        order.cancel_reason = reason
        order.expires_at = None
        order.save()
    if notify:
        emails.order_cancelled(order)
    return order


# ---- payment and fulfilment -----------------------------------------------

def confirm_payment(order, gateway="", transaction_id="", note=""):
    """Payment received (online, or bank transfer checked by staff). Safe to call twice."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.payment_status == PayStatus.PAID:
            return order
        laptops = list(Laptop.objects.select_for_update().filter(order_items__order=order))
        if order.status == Status.CANCELLED:
            usable = (Laptop.Status.AVAILABLE,)   # expired order: only revive if nobody else took the unit
        elif order.status == Status.PENDING:
            usable = (Laptop.Status.RESERVED,)
        else:
            raise OrderError("This order is not waiting for payment.")
        Payment.objects.create(order=order, method=order.payment_method, gateway=gateway,
                               transaction_id=transaction_id, amount=order.total,
                               status=Payment.Status.SUCCESS, note=note)
        if all(l.status in usable for l in laptops):
            Laptop.objects.filter(pk__in=[l.pk for l in laptops]).update(status=Laptop.Status.SOLD)
            order.status = Status.CONFIRMED
            order.payment_status = PayStatus.PAID
            order.confirmed_at = timezone.now()
            order.expires_at = None
            order.cancel_reason = ""
        else:  # paid after the reservation expired and the unit is gone
            order.payment_status = PayStatus.REFUND_DUE
        order.save()
    if order.status == Status.CONFIRMED:
        emails.order_confirmed(order)
    return order


def record_failed_payment(order, gateway="", transaction_id="", note=""):
    Payment.objects.create(order=order, method=order.payment_method, gateway=gateway,
                           transaction_id=transaction_id, amount=order.total,
                           status=Payment.Status.FAILED, note=note)


def confirm_cod(order):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status != Status.PENDING or order.payment_method != Order.Method.COD:
            raise OrderError("Only pending cash on delivery orders can be confirmed this way.")
        Laptop.objects.filter(order_items__order=order).update(status=Laptop.Status.SOLD)
        order.status = Status.CONFIRMED
        order.confirmed_at = timezone.now()
        order.expires_at = None
        order.save()
    emails.order_confirmed(order)
    return order


def _move(order, allowed_from, new_status, error):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status not in allowed_from:
            raise OrderError(error)
        order.status = new_status
        order.save()
    return order


def start_processing(order):
    return _move(order, (Status.CONFIRMED,), Status.PROCESSING, "Only confirmed orders can start processing.")


def mark_shipped(order):
    if order.delivery_method == Order.Delivery.DELIVERY:
        shipment = getattr(order, "shipment", None)
        if not shipment or not shipment.courier or not shipment.tracking_no:
            raise OrderError("Enter the courier and tracking number in the Shipment section and save first.")
    order = _move(order, (Status.CONFIRMED, Status.PROCESSING), Status.SHIPPED,
                  "Only confirmed or processing orders can be shipped.")
    shipment, _ = Shipment.objects.get_or_create(order=order)
    shipment.shipped_at = timezone.now()
    shipment.save()
    emails.order_shipped(order)
    return order


def mark_delivered(order):
    order = _move(order, (Status.CONFIRMED, Status.PROCESSING, Status.SHIPPED), Status.DELIVERED,
                  "This order cannot be marked delivered yet.")
    shipment, _ = Shipment.objects.get_or_create(order=order)
    shipment.delivered_at = timezone.now()
    shipment.save()
    if order.payment_method == Order.Method.COD and order.payment_status != PayStatus.PAID:
        Payment.objects.create(order=order, method=order.payment_method, gateway="cash",
                               amount=order.total, status=Payment.Status.SUCCESS, note="Collected on delivery")
        order.payment_status = PayStatus.PAID
        order.save()
    return order


def mark_refunded(order):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.payment_status not in (PayStatus.PAID, PayStatus.REFUND_DUE):
            raise OrderError("There is no payment to refund on this order.")
        order.payment_status = PayStatus.REFUNDED
        if order.status != Status.CANCELLED:
            order.status = Status.REFUNDED
        order.save()
    return order


ACTIONS = {
    "confirm_payment": ("Mark payment received", lambda o: confirm_payment(o, gateway="manual", transaction_id=o.bank_reference)),
    "confirm_cod": ("Confirm cash on delivery order", confirm_cod),
    "processing": ("Start processing", start_processing),
    "shipped": ("Mark shipped / ready for pickup", mark_shipped),
    "delivered": ("Mark delivered", mark_delivered),
    "cancel": ("Cancel and release laptops", lambda o: cancel_order(o, reason="Cancelled by staff")),
    "refunded": ("Mark refund paid", mark_refunded),
}


def available_actions(order):
    """Which buttons make sense for this order right now."""
    keys = []
    if order.status == Status.PENDING:
        keys.append("confirm_cod" if order.payment_method == Order.Method.COD else "confirm_payment")
    if order.status == Status.CONFIRMED:
        keys.append("processing")
    if order.status in (Status.CONFIRMED, Status.PROCESSING):
        keys.append("shipped")
    if order.status == Status.SHIPPED:
        keys.append("delivered")
    if order.status in (Status.PENDING, Status.CONFIRMED, Status.PROCESSING):
        keys.append("cancel")
    if order.status == Status.DELIVERED and order.payment_status == PayStatus.PAID:
        keys.append("refunded")
    if order.payment_status == PayStatus.REFUND_DUE:
        keys.append("refunded")
    return [(k, ACTIONS[k][0]) for k in keys]


# ---- returns --------------------------------------------------------------

def open_return(order_item, reason):
    """A customer sent a laptop back: take it off sale and track the investigation."""
    case = ReturnCase.objects.create(order_item=order_item, reason=reason)
    Laptop.objects.filter(pk=order_item.laptop_id).update(status=Laptop.Status.UNDER_INVESTIGATION)
    return case


def close_return(case, fixed):
    if case.outcome != ReturnCase.Outcome.OPEN:
        raise OrderError("This return case is already closed.")
    laptop_status = Laptop.Status.AVAILABLE if fixed else Laptop.Status.RETURNED
    Laptop.objects.filter(pk=case.order_item.laptop_id).update(status=laptop_status)
    case.outcome = ReturnCase.Outcome.FIXED if fixed else ReturnCase.Outcome.TO_SUPPLIER
    case.closed_at = timezone.now()
    case.save()
    return case


def mark_under_investigation(case):
    """Called when staff open a return case from the admin."""
    Laptop.objects.filter(pk=case.order_item.laptop_id).update(status=Laptop.Status.UNDER_INVESTIGATION)
