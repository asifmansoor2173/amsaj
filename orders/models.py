import secrets

from django.db import models

from inventory.models import Laptop

from .constants import STATE_CHOICES


def new_token():
    return secrets.token_urlsafe(16)


class ShippingZone(models.Model):
    """Delivery fee and timing for a group of states. One zone can be the 'rest of India' default."""

    name = models.CharField(max_length=80)
    states = models.TextField(blank=True, help_text="One state per line. Leave empty for the default zone.")
    fee = models.DecimalField("Delivery fee (INR)", max_digits=8, decimal_places=2, default=0)
    eta = models.CharField("Estimated delivery time", max_length=60, blank=True, help_text="e.g. 3-5 working days")
    schedule = models.CharField("Delivery schedule", max_length=120, blank=True, help_text="e.g. Deliveries Monday to Saturday")
    is_default = models.BooleanField("Default zone (states not listed elsewhere)", default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def state_list(self):
        return [s.strip().lower() for s in self.states.splitlines() if s.strip()]

    @classmethod
    def for_state(cls, state):
        zones = list(cls.objects.filter(active=True))
        wanted = (state or "").strip().lower()
        for zone in zones:
            if wanted and wanted in zone.state_list():
                return zone
        for zone in zones:
            if zone.is_default:
                return zone
        return None


class PickupLocation(models.Model):
    name = models.CharField(max_length=80)
    address = models.TextField()
    schedule = models.CharField("Pickup schedule", max_length=160, blank=True, help_text="e.g. Monday to Saturday, 10 am to 6 pm")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting payment or confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped / ready for pickup"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        REFUND_DUE = "refund_due", "Refund due"
        REFUNDED = "refunded", "Refunded"

    class Method(models.TextChoices):
        CARD = "card", "Card / online payment"
        COD = "cod", "Cash on delivery"
        BANK = "bank_transfer", "Bank transfer"

    class Delivery(models.TextChoices):
        DELIVERY = "delivery", "Home delivery"
        PICKUP = "pickup", "Pickup"

    order_no = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    transaction_id = models.CharField(
        max_length=20, blank=True, db_index=True, editable=False,
        help_text="Links every log entry of this order, from the first cart click to delivery and returns.")
    access_token = models.CharField(max_length=40, unique=True, default=new_token, editable=False)

    customer_name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=15)

    delivery_method = models.CharField(max_length=10, choices=Delivery.choices, default=Delivery.DELIVERY)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=60, choices=STATE_CHOICES, blank=True)
    pin_code = models.CharField(max_length=6, blank=True)
    shipping_zone = models.ForeignKey(ShippingZone, on_delete=models.PROTECT, null=True, blank=True)
    pickup_location = models.ForeignKey(PickupLocation, on_delete=models.PROTECT, null=True, blank=True)

    payment_method = models.CharField(max_length=15, choices=Method.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    payment_status = models.CharField(max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    bank_reference = models.CharField("Bank transfer reference", max_length=80, blank=True)
    customer_notes = models.TextField(blank=True)
    staff_notes = models.TextField(blank=True)
    cancel_reason = models.CharField(max_length=120, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="When the reservation is released if unpaid.")
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_no or f"Order {self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.order_no:
            self.order_no = f"ORD-{self.pk:06d}"
            super().save(update_fields=["order_no"])

    @property
    def address_text(self):
        if self.delivery_method == self.Delivery.PICKUP:
            if self.pickup_location:
                return f"Pickup: {self.pickup_location.name}, {self.pickup_location.address}"
            return "Pickup"
        parts = [self.address_line1, self.address_line2, self.city, f"{self.state} {self.pin_code}".strip()]
        return ", ".join(p for p in parts if p)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    laptop = models.ForeignKey(Laptop, on_delete=models.PROTECT, related_name="order_items")
    sku = models.CharField(max_length=20)
    title = models.CharField(max_length=200)
    price = models.DecimalField("Price at purchase", max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.order.order_no} | {self.sku} | {self.title}"


class Payment(models.Model):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=15, choices=Order.Method.choices)
    gateway = models.CharField(max_length=30, blank=True)
    transaction_id = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.order_no} {self.get_status_display()} {self.amount}"


class Shipment(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="shipment")
    courier = models.CharField(max_length=80, blank=True)
    tracking_no = models.CharField("Tracking number", max_length=80, blank=True)
    planned_date = models.DateField("Planned delivery or pickup date", null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Shipment for {self.order.order_no}"


class ReturnCase(models.Model):
    class Outcome(models.TextChoices):
        OPEN = "open", "Under investigation"
        FIXED = "fixed", "Fixed - back in stock"
        TO_SUPPLIER = "returned_to_supplier", "Sent back to supplier"

    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="return_cases")
    reason = models.TextField()
    outcome = models.CharField(max_length=22, choices=Outcome.choices, default=Outcome.OPEN)
    notes = models.TextField(blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"Return: {self.order_item}"

    @property
    def laptop(self):
        return self.order_item.laptop
