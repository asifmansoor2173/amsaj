import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from activity import logger as activity
from inventory.models import Laptop
from store.templatetags.store_extras import inr

from . import gateways, services
from .cart import Cart
from .forms import CheckoutForm, TrackForm
from .models import Order, PickupLocation
from .services import OrderError


# ---- cart -----------------------------------------------------------------

def cart_view(request):
    cart = Cart(request)
    laptops, dropped = cart.cleaned()
    for title in dropped:
        messages.warning(request, f"{title} is no longer available and was removed from your cart.")
        activity.log("cart.item_dropped", f"{title} removed from the cart because it is no longer available",
                     level="WARNING", details={"title": title})
    subtotal = sum(l.price for l in laptops)
    return render(request, "orders/cart.html", {"laptops": laptops, "subtotal": subtotal})


@require_POST
def cart_add(request, pk):
    laptop = get_object_or_404(Laptop, pk=pk)
    if laptop.status != Laptop.Status.AVAILABLE:
        activity.log("cart.add_rejected", f"{laptop.sku} could not be added: {laptop.status}", level="WARNING",
                     laptop=laptop, details={"status": laptop.status})
        messages.error(request, "Sorry, this laptop is not available right now.")
        return redirect("laptop_detail", pk=pk) if laptop.status in ("reserved", "sold") else redirect("laptop_list")
    cart = Cart(request)
    if cart.add(pk):
        activity.log("cart.item_added", f"{laptop.sku} added to the cart", laptop=laptop,
                     details={"price": laptop.price, "cart_size": len(cart)})
        messages.success(request, f"{laptop.brand} {laptop.model} was added to your cart.")
    else:
        messages.info(request, "This laptop is already in your cart.")
    return redirect("cart")


@require_POST
def cart_remove(request, pk):
    cart = Cart(request)
    cart.remove(pk)
    laptop = Laptop.objects.filter(pk=pk).first()
    activity.log("cart.item_removed", f"{laptop.sku if laptop else pk} removed from the cart", laptop=laptop,
                 details={"cart_size": len(cart)})
    return redirect("cart")


# ---- checkout -------------------------------------------------------------

def _checkout_context(form, laptops, subtotal):
    pickup_data = {str(l.pk): {"name": l.name, "address": l.address, "schedule": l.schedule}
                   for l in PickupLocation.objects.filter(active=True)}
    return {"form": form, "laptops": laptops, "subtotal": subtotal, "pickup_data": pickup_data}


def checkout(request):
    cart = Cart(request)
    laptops, dropped = cart.cleaned()
    for title in dropped:
        messages.warning(request, f"{title} is no longer available and was removed from your cart.")
        activity.log("cart.item_dropped", f"{title} removed because it is no longer available", level="WARNING",
                     details={"title": title})
    if not laptops:
        return redirect("cart")
    subtotal = sum(l.price for l in laptops)

    form = CheckoutForm(request.POST or None)
    if request.method == "POST" and not form.is_valid():
        activity.log("checkout.validation_failed", "Checkout form had errors", level="WARNING",
                     details={"fields": sorted(form.errors.keys())})
    if request.method == "POST" and form.is_valid():
        try:
            order = services.create_order([l.pk for l in laptops], form.order_data(),
                                          transaction_id=activity.journey_id())
        except OrderError as error:
            activity.log("order.failed", f"Order could not be placed: {error}", level="WARNING",
                         details={"reason": str(error), "laptops": [l.sku for l in laptops],
                                  "payment_method": form.cleaned_data.get("payment_method")})
            messages.error(request, str(error))
            return redirect("cart") if "no longer" in str(error) else render(
                request, "orders/checkout.html", _checkout_context(form, laptops, subtotal))
        cart.clear()
        activity.end_journey()
        if order.payment_method == Order.Method.CARD:
            activity.log("payment.gateway_redirect", f"Customer sent to the {settings.PAYMENT_GATEWAY} payment page",
                         order=order, details={"gateway": settings.PAYMENT_GATEWAY, "amount": order.total})
            return redirect(gateways.card_payment_url(order))
        return redirect("order_detail", token=order.access_token)
    return render(request, "orders/checkout.html", _checkout_context(form, laptops, subtotal))


def checkout_quote(request):
    """Used by the checkout page to show the delivery fee before the order is placed."""
    method = request.GET.get("method", "delivery")
    try:
        zone, location, fee = services.quote_shipping(method, request.GET.get("state", ""),
                                                      request.GET.get("pickup") or None)
    except OrderError as error:
        return JsonResponse({"ok": False, "message": str(error)})
    detail = zone.eta if zone else (location.schedule if location else "")
    return JsonResponse({"ok": True, "fee": str(fee), "fee_display": inr(fee) if fee else "Free", "detail": detail})


# ---- orders ---------------------------------------------------------------

def _order_by_token(token):
    order = get_object_or_404(Order.objects.prefetch_related("items", "payments")
                              .select_related("shipping_zone", "pickup_location"), access_token=token)
    activity.bind_order(order)   # everything in this request becomes part of the order's trace
    return order


def order_detail(request, token):
    order = _order_by_token(token)
    activity.log("order.viewed", f"Order page {order.order_no} opened", order=order,
                 details={"status": order.status, "payment_status": order.payment_status})
    bank = {k: v for k, v in settings.BANK_DETAILS.items() if v}
    return render(request, "orders/order_detail.html", {"order": order, "bank": bank})


@require_POST
def order_bank_reference(request, token):
    order = _order_by_token(token)
    if order.payment_method == Order.Method.BANK and order.status == Order.Status.PENDING:
        order.bank_reference = request.POST.get("bank_reference", "").strip()[:80]
        order.save(update_fields=["bank_reference", "updated_at"])
        activity.log("payment.bank_reference_submitted", f"Bank transfer reference given for {order.order_no}",
                     order=order, details={"bank_reference": order.bank_reference})
        messages.success(request, "Thank you. We will check your transfer and confirm the order.")
    return redirect("order_detail", token=token)


def order_track(request):
    form = TrackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        digits = re.sub(r"\D", "", form.cleaned_data["phone"])[-10:]
        order = Order.objects.filter(order_no__iexact=form.cleaned_data["order_no"].strip()).first()
        if order and order.phone[-10:] == digits:
            activity.log("track.lookup", f"Order {order.order_no} found by lookup", order=order,
                         details={"found": True, "phone": activity.mask_phone(digits)})
            return redirect("order_detail", token=order.access_token)
        activity.log("track.lookup_failed", "Order lookup found nothing", level="WARNING",
                     details={"order_no": form.cleaned_data["order_no"].strip(), "phone": activity.mask_phone(digits)})
        messages.error(request, "We could not find an order with those details.")
    return render(request, "orders/track.html", {"form": form})


# ---- test card gateway (local development only) ---------------------------

def test_gateway(request, token):
    if settings.PAYMENT_GATEWAY != "test":
        raise Http404()
    order = _order_by_token(token)
    if order.payment_status == Order.PaymentStatus.PAID:
        return redirect("order_detail", token=token)
    if request.method == "POST":
        if request.POST.get("outcome") == "success":
            order = services.confirm_payment(order, gateway="test", transaction_id=f"TEST-{uuid.uuid4().hex[:10]}")
            if order.payment_status == Order.PaymentStatus.REFUND_DUE:
                messages.warning(request, "Your payment arrived after the reservation expired and the laptop was sold. We will refund you.")
            else:
                messages.success(request, "Payment received. Your order is confirmed.")
        else:
            services.record_failed_payment(order, gateway="test", note="Customer cancelled the test payment")
            messages.error(request, "The payment was not completed. You can try again from your order page.")
        return redirect("order_detail", token=token)
    return render(request, "orders/test_gateway.html", {"order": order})
