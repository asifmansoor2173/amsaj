import uuid

from django.db import models
from django.utils import timezone


def new_event_id():
    return uuid.uuid4().hex


class ActivityLog(models.Model):
    """One row for every activity in the system. Rows are never edited.

    * event_id        unique id of this single log row
    * transaction_id  shared by every row of the same flow, so one search shows the whole story
                      (for a customer: cart, checkout, payment, shipping, returns of one order)
    * request_id      shared by every row created during one web request
    """

    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        ERROR = "ERROR", "Error"

    class ActorType(models.TextChoices):
        VISITOR = "visitor", "Visitor"
        CUSTOMER = "customer", "Customer"
        STAFF = "staff", "Staff"
        SYSTEM = "system", "System"

    event_id = models.CharField(max_length=32, unique=True, editable=False, default=new_event_id)
    transaction_id = models.CharField(max_length=20, db_index=True)
    request_id = models.CharField(max_length=32, blank=True, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    level = models.CharField(max_length=8, choices=Level.choices, default=Level.INFO, db_index=True)
    category = models.CharField(max_length=20, db_index=True)
    action = models.CharField(max_length=60, db_index=True)
    message = models.CharField(max_length=400, blank=True)

    actor_type = models.CharField(max_length=10, choices=ActorType.choices, default=ActorType.SYSTEM)
    actor_name = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    http_method = models.CharField(max_length=8, blank=True)
    path = models.CharField(max_length=300, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    order_no = models.CharField(max_length=20, blank=True, db_index=True)
    laptop_sku = models.CharField(max_length=20, blank=True, db_index=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)

    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "activity log entry"
        verbose_name_plural = "activity log"
        indexes = [models.Index(fields=["transaction_id", "occurred_at"])]

    def __str__(self):
        return f"{self.occurred_at:%Y-%m-%d %H:%M:%S} {self.transaction_id} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Activity log entries cannot be changed.")
        super().save(*args, **kwargs)
