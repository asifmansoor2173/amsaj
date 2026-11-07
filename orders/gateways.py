"""Card payments. Only the built-in TEST gateway exists so far.

When you are ready to take real card payments, add a class here for the gateway you choose
(Razorpay, Cashfree or PayU), set PAYMENT_GATEWAY to its name, and have its webhook call
services.confirm_payment(order, gateway=..., transaction_id=...).
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse


def card_payment_url(order):
    """Where to send the customer to pay for this order by card."""
    if settings.PAYMENT_GATEWAY == "test":
        return reverse("test_gateway", args=[order.access_token])
    raise ImproperlyConfigured(f"Payment gateway '{settings.PAYMENT_GATEWAY}' is not set up yet.")
