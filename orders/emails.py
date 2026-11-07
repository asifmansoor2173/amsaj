from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from store.templatetags.store_extras import inr


def order_url(order):
    return f"{settings.SITE_URL}{reverse('order_detail', args=[order.access_token])}"


def _lines(order):
    return "\n".join(f"- {i.title} ({i.sku}): {inr(i.price)}" for i in order.items.all())


def _send(order, subject, body):
    footer = (f"\nView your order: {order_url(order)}\n\n"
              f"{settings.STORE_NAME}\n2-month warranty and 7-day returns on every laptop.")
    send_mail(f"{subject} - {order.order_no}", body + footer, settings.DEFAULT_FROM_EMAIL,
              [order.email], fail_silently=True)


def order_placed(order):
    if order.payment_method == order.Method.BANK:
        how = "Please pay by bank transfer using the details on your order page and enter your transfer reference there."
    elif order.payment_method == order.Method.COD:
        how = "You will pay in cash on delivery. Our team will contact you to confirm the order."
    else:
        how = "Please complete your online payment to confirm the order."
    _send(order, "We received your order",
          f"Hello {order.customer_name},\n\nThank you for your order {order.order_no}.\n\n{_lines(order)}\n\n"
          f"Total: {inr(order.total)} (delivery {inr(order.shipping_fee)})\n{how}\n"
          f"The laptop is reserved for you until {order.expires_at:%d %b %Y, %I:%M %p}.\n")


def order_confirmed(order):
    _send(order, "Your order is confirmed",
          f"Hello {order.customer_name},\n\nYour order {order.order_no} is confirmed.\n\n{_lines(order)}\n\n"
          f"Total: {inr(order.total)}\nWe will prepare it and let you know when it ships.\n")


def order_shipped(order):
    shipment = getattr(order, "shipment", None)
    if order.delivery_method == order.Delivery.PICKUP:
        detail = f"It is ready for pickup at {order.pickup_location}."
    else:
        detail = f"Courier: {shipment.courier or '-'}\nTracking number: {shipment.tracking_no or '-'}" if shipment else ""
    _send(order, "Your order is on its way" if order.delivery_method == order.Delivery.DELIVERY else "Your order is ready",
          f"Hello {order.customer_name},\n\nOrder {order.order_no} update.\n{detail}\n")


def order_cancelled(order):
    _send(order, "Your order was cancelled",
          f"Hello {order.customer_name},\n\nOrder {order.order_no} was cancelled"
          f"{': ' + order.cancel_reason if order.cancel_reason else ''}.\n"
          + ("If you already paid, we will refund you.\n" if order.payment_status == order.PaymentStatus.REFUND_DUE else ""))
