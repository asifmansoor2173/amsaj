"""All business rules for orders live here. Views and admin screens only call these functions.

Every important step is written to the activity log (file + database). All the steps of one order
share the order's transaction_id, so one search shows its complete history.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from activity import logger as activity
from inventory.models import Laptop
from inventory.status import set_status

from . import emails
from .models import Order, OrderItem, Payment, PickupLocation, ReturnCase, Shipment, ShippingZone

Status = Order.Status
PayStatus = Order.PaymentStatus
LS = Laptop.Status


class OrderError(Exception):
    """Raised with a message that is safe to show to the user."""


def _log(order, action, message, old_status, old_pay, level="INFO", **details):
    """Log an order step with the before/after status."""
    details["status"] = {"from": old_status, "to": order.status}
    details["payment_status"] = {"from": old_pay, "to": order.payment_status}
    activity.log(action, message, level=level, order=order, object_type="order", object_id=order.pk, details=details)


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


def create_order(laptop_ids, data, transaction_id=None):
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
        gone = [f"{l.brand} {l.model}" for l in laptops if l.status != LS.AVAILABLE]
        if gone:
            raise OrderError("Sorry, " + ", ".join(gone) + " is no longer available. It was removed from your cart.")
        subtotal = sum(l.price for l in laptops)
        order = Order.objects.create(
            transaction_id=transaction_id or activity.new_transaction_id(),
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
        activity.bind_order(order)
        activity.log("order.created", f"Order {order.order_no} placed", order=order, object_type="order",
                     object_id=order.pk, details={
                         "items": [{"sku": l.sku, "title": l.title, "price": l.price} for l in laptops],
                         "subtotal": subtotal, "shipping_fee": fee, "total": order.total,
                         "payment_method": order.payment_method, "delivery_method": order.delivery_method,
                         "shipping_zone": zone.name if zone else "", "pickup_location": location.name if location else "",
                         "state": order.state, "customer_email": activity.mask_email(order.email),
                         "customer_phone": activity.mask_phone(order.phone),
                         "reserved_until": order.expires_at,
                     })
        set_status(Laptop.objects.filter(pk__in=ids), LS.RESERVED, "order placed", order=order)
    emails.order_placed(order)
    return order


def release_expired(now=None):
    """Cancel unpaid orders whose reservation time has run out and put the laptops back on sale."""
    now = now or timezone.now()
    ids = list(Order.objects.filter(status=Status.PENDING, expires_at__lt=now).values_list("pk", flat=True))
    for pk in ids:
        try:
            cancel_order(Order.objects.get(pk=pk), reason="Reservation expired", notify=False, by_system=True)
        except OrderError:
            pass
    return len(ids)


def cancel_order(order, reason="", notify=True, by_system=False):
    actor = {"actor_type": "system", "actor_name": "reservation timer"} if by_system else {}
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status not in (Status.PENDING, Status.CONFIRMED, Status.PROCESSING):
            raise OrderError("This order can no longer be cancelled. Use a return case instead.")
        old_status, old_pay = order.status, order.payment_status
        released = set_status(Laptop.objects.filter(order_items__order=order, status__in=[LS.RESERVED, LS.SOLD]),
                              LS.AVAILABLE, reason or "order cancelled", order=order, **actor)
        if order.payment_status == PayStatus.PAID:
            order.payment_status = PayStatus.REFUND_DUE
        order.status = Status.CANCELLED
        order.cancel_reason = reason
        order.expires_at = None
        order.save()
        activity.log("order.expired" if by_system else "order.cancelled", f"Order {order.order_no} cancelled: {reason}",
                     order=order, object_type="order", object_id=order.pk, level="INFO" if not by_system else "WARNING",
                     details={"reason": reason, "laptops_released": released,
                              "status": {"from": old_status, "to": order.status},
                              "payment_status": {"from": old_pay, "to": order.payment_status}}, **actor)
    if notify:
        emails.order_cancelled(order)
    return order


# ---- payment and fulfilment -----------------------------------------------

def confirm_payment(order, gateway="", transaction_id="", note=""):
    """Payment received (online, or bank transfer checked by staff). Safe to call twice."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.payment_status == PayStatus.PAID:
            activity.log("payment.duplicate_ignored", "Payment notification repeated; ignored", level="WARNING",
                         order=order, details={"gateway": gateway, "transaction_id": transaction_id})
            return order
        old_status, old_pay = order.status, order.payment_status
        laptops = list(Laptop.objects.select_for_update().filter(order_items__order=order))
        if order.status == Status.CANCELLED:
            usable = (LS.AVAILABLE,)   # expired order: only revive if nobody else took the unit
        elif order.status == Status.PENDING:
            usable = (LS.RESERVED,)
        else:
            raise OrderError("This order is not waiting for payment.")
        Payment.objects.create(order=order, method=order.payment_method, gateway=gateway,
                               transaction_id=transaction_id, amount=order.total,
                               status=Payment.Status.SUCCESS, note=note)
        if all(l.status in usable for l in laptops):
            set_status(Laptop.objects.filter(pk__in=[l.pk for l in laptops]), LS.SOLD, "payment received", order=order)
            order.status = Status.CONFIRMED
            order.payment_status = PayStatus.PAID
            order.confirmed_at = timezone.now()
            order.expires_at = None
            order.cancel_reason = ""
            order.save()
            _log(order, "payment.confirmed", f"Payment of {order.total} received for {order.order_no}",
                 old_status, old_pay, gateway=gateway, transaction_id=transaction_id, amount=order.total,
                 revived_after_expiry=(old_status == Status.CANCELLED))
        else:  # paid after the reservation expired and the unit is gone
            order.payment_status = PayStatus.REFUND_DUE
            order.save()
            _log(order, "payment.refund_due", f"Payment received for {order.order_no} but the laptop is gone: refund due",
                 old_status, old_pay, level="WARNING", gateway=gateway, transaction_id=transaction_id, amount=order.total)
    if order.status == Status.CONFIRMED:
        emails.order_confirmed(order)
    return order


def record_failed_payment(order, gateway="", transaction_id="", note=""):
    Payment.objects.create(order=order, method=order.payment_method, gateway=gateway,
                           transaction_id=transaction_id, amount=order.total,
                           status=Payment.Status.FAILED, note=note)
    activity.log("payment.failed", f"Payment failed or cancelled for {order.order_no}", level="WARNING", order=order,
                 details={"gateway": gateway, "transaction_id": transaction_id, "note": note, "amount": order.total})


def confirm_cod(order):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status != Status.PENDING or order.payment_method != Order.Method.COD:
            raise OrderError("Only pending cash on delivery orders can be confirmed this way.")
        old_status, old_pay = order.status, order.payment_status
        set_status(Laptop.objects.filter(order_items__order=order), LS.SOLD, "cash on delivery confirmed", order=order)
        order.status = Status.CONFIRMED
        order.confirmed_at = timezone.now()
        order.expires_at = None
        order.save()
        _log(order, "order.cod_confirmed", f"Cash on delivery order {order.order_no} confirmed", old_status, old_pay)
    emails.order_confirmed(order)
    return order


def _move(order, allowed_from, new_status, error, action, message, **details):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status not in allowed_from:
            raise OrderError(error)
        old_status, old_pay = order.status, order.payment_status
        order.status = new_status
        order.save()
        _log(order, action, message.format(order=order), old_status, old_pay, **details)
    return order


def start_processing(order):
    return _move(order, (Status.CONFIRMED,), Status.PROCESSING, "Only confirmed orders can start processing.",
                 "order.processing", "Order {order.order_no} is being prepared")


def mark_shipped(order):
    shipment = getattr(order, "shipment", None)
    if order.delivery_method == Order.Delivery.DELIVERY:
        if not shipment or not shipment.courier or not shipment.tracking_no:
            raise OrderError("Enter the courier and tracking number in the Shipment section and save first.")
    order = _move(order, (Status.CONFIRMED, Status.PROCESSING), Status.SHIPPED,
                  "Only confirmed or processing orders can be shipped.",
                  "order.shipped", "Order {order.order_no} shipped or ready for pickup",
                  courier=getattr(shipment, "courier", ""), tracking_no=getattr(shipment, "tracking_no", ""),
                  delivery_method=order.delivery_method)
    shipment, _ = Shipment.objects.get_or_create(order=order)
    shipment.shipped_at = timezone.now()
    shipment.save()
    emails.order_shipped(order)
    return order


def mark_delivered(order):
    order = _move(order, (Status.CONFIRMED, Status.PROCESSING, Status.SHIPPED), Status.DELIVERED,
                  "This order cannot be marked delivered yet.", "order.delivered", "Order {order.order_no} delivered")
    shipment, _ = Shipment.objects.get_or_create(order=order)
    shipment.delivered_at = timezone.now()
    shipment.save()
    if order.payment_method == Order.Method.COD and order.payment_status != PayStatus.PAID:
        old_pay = order.payment_status
        Payment.objects.create(order=order, method=order.payment_method, gateway="cash",
                               amount=order.total, status=Payment.Status.SUCCESS, note="Collected on delivery")
        order.payment_status = PayStatus.PAID
        order.save()
        activity.log("payment.cod_collected", f"Cash {order.total} collected on delivery for {order.order_no}",
                     order=order, details={"amount": order.total, "payment_status": {"from": old_pay, "to": "paid"}})
    return order


def mark_refunded(order):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.payment_status not in (PayStatus.PAID, PayStatus.REFUND_DUE):
            raise OrderError("There is no payment to refund on this order.")
        old_status, old_pay = order.status, order.payment_status
        order.payment_status = PayStatus.REFUNDED
        if order.status != Status.CANCELLED:
            order.status = Status.REFUNDED
        order.save()
        _log(order, "payment.refunded", f"Refund of {order.total} recorded for {order.order_no}", old_status, old_pay,
             amount=order.total)
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
    mark_under_investigation(case)
    return case


def mark_under_investigation(case):
    """Take the returned laptop off sale and log the new return case."""
    item = case.order_item
    set_status(Laptop.objects.filter(pk=item.laptop_id), LS.UNDER_INVESTIGATION, "customer return opened", order=item.order)
    activity.log("return.opened", f"Return opened for {item.sku} on {item.order.order_no}", order=item.order,
                 laptop_sku=item.sku, object_type="returncase", object_id=case.pk, details={"reason": case.reason})


def close_return(case, fixed):
    if case.outcome != ReturnCase.Outcome.OPEN:
        raise OrderError("This return case is already closed.")
    item = case.order_item
    new_status = LS.AVAILABLE if fixed else LS.RETURNED
    set_status(Laptop.objects.filter(pk=item.laptop_id), new_status,
               "return fixed, back in stock" if fixed else "not fixable, sent back to supplier", order=item.order)
    case.outcome = ReturnCase.Outcome.FIXED if fixed else ReturnCase.Outcome.TO_SUPPLIER
    case.closed_at = timezone.now()
    case.save()
    activity.log("return.closed", f"Return for {item.sku} closed: {case.get_outcome_display()}", order=item.order,
                 laptop_sku=item.sku, object_type="returncase", object_id=case.pk,
                 details={"outcome": case.outcome, "laptop_status": new_status})
    return case
