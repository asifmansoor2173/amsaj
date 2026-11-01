import os
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from .media_utils import process_image
from .validators import media_kind, validate_media_file


class Brand(models.Model):
    name = models.CharField(max_length=60, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    """The party a laptop was bought from (unfixable returns go back here)."""

    name = models.CharField(max_length=120, unique=True)
    contact = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Laptop(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (not published)"
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        SOLD = "sold", "Sold"
        UNDER_INVESTIGATION = "under_investigation", "Under investigation"
        RETURNED = "returned", "Returned to supplier"

    class StorageType(models.TextChoices):
        SSD = "SSD", "SSD"
        NVME = "NVMe", "NVMe SSD"
        HDD = "HDD", "HDD"

    sku = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT)
    model = models.CharField(max_length=100)
    title = models.CharField(
        max_length=200, blank=True,
        help_text="Leave blank to build it automatically from brand, model, CPU, RAM and storage.",
    )
    description = models.TextField(blank=True)

    # Specifications
    cpu = models.CharField("CPU", max_length=100)
    gpu = models.CharField("GPU", max_length=100, blank=True)
    tpu = models.CharField("TPU / NPU", max_length=100, blank=True)
    ram_gb = models.PositiveIntegerField("RAM (GB)")
    storage_gb = models.PositiveIntegerField("Hard disk size (GB)")
    storage_type = models.CharField(max_length=10, choices=StorageType.choices, default=StorageType.SSD)
    screen_inches = models.DecimalField("Screen size (inches)", max_digits=3, decimal_places=1)
    resolution = models.CharField(max_length=30, blank=True, help_text="e.g. 1920x1080")
    os = models.CharField("Operating system", max_length=60, blank=True)
    battery_health_pct = models.PositiveSmallIntegerField("Battery health (%)", null=True, blank=True)
    keyboard_layout = models.CharField(max_length=30, blank=True)

    # Condition and tracking
    condition = models.TextField(help_text="Describe the condition, e.g. 'light scratches on the lid, keyboard like new'.")
    defects_notes = models.TextField(blank=True)
    serial_number = models.CharField(max_length=80, unique=True, null=True, blank=True)
    warranty_months = models.PositiveSmallIntegerField(default=2)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, blank=True,
                                 help_text="Who we bought this laptop from.")

    # Money (INR)
    purchase_cost = models.DecimalField("Purchase cost (INR, staff only)", max_digits=10, decimal_places=2, null=True, blank=True)
    price = models.DecimalField("Selling price (INR)", max_digits=10, decimal_places=2)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["price"]), models.Index(fields=["ram_gb"])]

    def __str__(self):
        return f"{self.sku} - {self.title}"

    def clean(self):
        if self.price is not None and self.price <= 0:
            raise ValidationError({"price": "Price must be greater than zero."})
        if self.battery_health_pct is not None and self.battery_health_pct > 100:
            raise ValidationError({"battery_health_pct": "Battery health cannot be above 100%."})

    def build_title(self):
        return f"{self.brand} {self.model} | {self.cpu} | {self.ram_gb}GB RAM | {self.storage_gb}GB {self.storage_type}"

    def save(self, *args, **kwargs):
        if not self.title:
            self.title = self.build_title()
        super().save(*args, **kwargs)
        if not self.sku:
            self.sku = f"AMS-{self.pk:06d}"
            super().save(update_fields=["sku"])

    @property
    def profit(self):
        if self.purchase_cost is None:
            return None
        return self.price - self.purchase_cost

    @property
    def has_photo(self):
        return self.media.filter(kind="image").exists()

    @property
    def cover(self):
        """Main photo, using prefetched media (no extra database query)."""
        photos = [m for m in self.media.all() if m.kind == "image"]
        if not photos:
            return None
        return sorted(photos, key=lambda m: (not m.is_primary, m.sort_order, m.id))[0]

    @property
    def photos(self):
        return sorted((m for m in self.media.all() if m.kind == "image"),
                      key=lambda m: (not m.is_primary, m.sort_order, m.id))

    @property
    def videos(self):
        return sorted((m for m in self.media.all() if m.kind == "video"),
                      key=lambda m: (m.sort_order, m.id))

    @property
    def primary_media(self):
        return self.media.filter(kind="image").order_by("-is_primary", "sort_order", "id").first()


def media_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"laptops/{instance.laptop_id}/{uuid.uuid4().hex}{ext}"


def thumb_upload_path(instance, filename):
    return f"laptops/{instance.laptop_id}/thumbs/{uuid.uuid4().hex}.webp"


class LaptopMedia(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Photo"
        VIDEO = "video", "Video"

    laptop = models.ForeignKey(Laptop, on_delete=models.CASCADE, related_name="media")
    kind = models.CharField(max_length=5, choices=Kind.choices, editable=False)
    file = models.FileField(upload_to=media_upload_path, validators=[validate_media_file])
    thumbnail = models.ImageField(upload_to=thumb_upload_path, blank=True, editable=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField("Main photo", default=False)
    alt_text = models.CharField(max_length=150, blank=True, help_text="e.g. 'Top view', 'Ports on left side'")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "photo / video"
        verbose_name_plural = "photos / videos"

    def __str__(self):
        return f"{self.get_kind_display()} for {self.laptop.sku}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_file_name = self.file.name if self.pk else None

    def save(self, *args, **kwargs):
        self.kind = media_kind(self.file.name) or self.kind
        file_changed = bool(self.file) and self.file.name != self._original_file_name
        if self.kind == "image" and file_changed:
            main, thumb = process_image(self.file.file)
            self.file.save("photo.webp", main, save=False)
            self.thumbnail.save("thumb.webp", thumb, save=False)
        super().save(*args, **kwargs)
        self._original_file_name = self.file.name
        if self.is_primary and self.kind == "image":
            self.laptop.media.exclude(pk=self.pk).update(is_primary=False)
