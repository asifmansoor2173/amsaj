import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from inventory.models import Laptop
from store.templatetags.store_extras import inr

from . import gateways, services
from .cart import Cart
from .forms import CheckoutForm, TrackForm
from .models import Order
from .services import OrderError


# ---- cart -----------------------------------------------------------------

def cart_view(request):
    cart = Cart(request)
    laptops, dropped = cart.cleaned()
    for title in dropped:
        messages.warning(request, f"{title} is no longer available and was removed from your cart.")
    subtotal = sum(l.price for l in laptops)
    return render(request, "orders/cart.html", {"laptops": laptops, "subtotal": subtotal})


@require_POST
def cart_add(request, pk):
    laptop = get_object_or_404(Laptop, pk=pk)
    if laptop.status != Laptop.Status.AVAILABLE:
        messages.error(request, "Sorry, this laptop is not available right now.")
        return redirect("laptop_detail", pk=pk) if laptop.status in ("reserved", "sold") else redirect("laptop_list")
    if Cart(request).add(pk):
        messages.success(request, f"{laptop.brand} {laptop.model} was added to your cart.")
    else:
        messages.info(request, "This laptop is already in your cart.")
    return redirect("cart")


@require_POST
def cart_remove(request, pk):
    Cart(request).remove(pk)
    return redirect("cart")


# ---- checkout -------------------------------------------------------------

def checkout(request):
    cart = Cart(request)
    laptops, dropped = cart.cleaned()
    for title in dropped:
        messages.warning(request, f"{title} is no longer available and was removed from your cart.")
    if not laptops:
        return redirect("cart")
    subtotal = sum(l.price for l in laptops)

    form = CheckoutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            order = services.create_order([l.pk for l in laptops], form.order_data())
        except OrderError as error:
            messages.error(request, str(error))
            return redirect("cart") if "no longer" in str(error) else render(
                request, "orders/checkout.html", {"form": form, "laptops": laptops, "subtotal": subtotal})
        cart.clear()
        if order.payment_method == Order.Method.CARD:
            return redirect(gateways.card_payment_url(order))
        return redirect("order_detail", token=order.access_token)
    return render(request, "orders/checkout.html", {"form": form, "laptops": laptops, "subtotal": subtotal})


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
    return get_object_or_404(Order.objects.prefetch_related("items", "payments")
                             .select_related("shipping_zone", "pickup_location"), access_token=token)


def order_detail(request, token):
    order = _order_by_token(token)
    bank = {k: v for k, v in settings.BANK_DETAILS.items() if v}
    return render(request, "orders/order_detail.html", {"order": order, "bank": bank})


@require_POST
def order_bank_reference(request, token):
    order = _order_by_token(token)
    if order.payment_method == Order.Method.BANK and order.status == Order.Status.PENDING:
        order.bank_reference = request.POST.get("bank_reference", "").strip()[:80]
        order.save(update_fields=["bank_reference", "updated_at"])
        messages.success(request, "Thank you. We will check your transfer and confirm the order.")
    return redirect("order_detail", token=token)


def order_track(request):
    form = TrackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        digits = re.sub(r"\D", "", form.cleaned_data["phone"])[-10:]
        order = Order.objects.filter(order_no__iexact=form.cleaned_data["order_no"].strip()).first()
        if order and order.phone[-10:] == digits:
            return redirect("order_detail", token=order.access_token)
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
