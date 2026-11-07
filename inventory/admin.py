import csv

from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from activity import logger as activity
from activity.admin_mixin import AuditedAdminMixin

from .forms import BulkMediaUploadForm
from .models import Brand, Laptop, LaptopMedia, Supplier
from .status import set_status

admin.site.site_header = "AmSaJ Used Laptops"
admin.site.site_title = "AmSaJ Admin"
admin.site.index_title = "Inventory and orders"

SHOT_LIST = (
    "Suggested photos: top lid, open with screen on, keyboard, left ports, right ports, "
    "bottom, any defects, battery health screenshot."
)


@admin.register(Brand)
class BrandAdmin(AuditedAdminMixin, admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Supplier)
class SupplierAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "contact"]
    search_fields = ["name", "contact"]


class LaptopMediaInline(admin.TabularInline):
    model = LaptopMedia
    extra = 0
    fields = ["preview", "file", "alt_text", "sort_order", "is_primary"]
    readonly_fields = ["preview"]

    @admin.display(description="Preview")
    def preview(self, obj):
        if not obj.pk:
            return "-"
        if obj.kind == "image" and obj.thumbnail:
            return format_html('<img src="{}" style="width:96px;height:72px;object-fit:contain;background:#fff;border:1px solid #ddd;border-radius:4px">', obj.thumbnail.url)
        if obj.kind == "video":
            return format_html('<video src="{}" style="width:96px;height:72px;object-fit:contain;background:#000" controls preload="none"></video>', obj.file.url)
        return "-"


@admin.action(description="Mark selected as Available (needs at least one photo)")
def mark_available(modeladmin, request, queryset):
    done = 0
    for laptop in queryset:
        if laptop.has_photo:
            set_status(Laptop.objects.filter(pk=laptop.pk), Laptop.Status.AVAILABLE, "published by staff")
            done += 1
        else:
            modeladmin.message_user(request, f"{laptop.sku} has no photo, so it was not published.", messages.WARNING)
    if done:
        modeladmin.message_user(request, f"{done} laptop(s) marked Available.")


def _status_action(status, label):
    @admin.action(description=f"Mark selected as {label}")
    def action(modeladmin, request, queryset):
        count = set_status(queryset, status, f"staff action: {label}")
        modeladmin.message_user(request, f"{count} laptop(s) marked {label}.")

    action.__name__ = f"mark_{status}"
    return action


mark_draft = _status_action(Laptop.Status.DRAFT, "Draft (hidden from the shop)")
mark_investigation = _status_action(Laptop.Status.UNDER_INVESTIGATION, "Under investigation")
mark_returned = _status_action(Laptop.Status.RETURNED, "Returned to supplier")


@admin.action(description="Duplicate selected (copies the specs, not photos)")
def duplicate_laptops(modeladmin, request, queryset):
    for laptop in queryset:
        source_sku = laptop.sku
        laptop.pk = None
        laptop.sku = ""
        laptop.serial_number = None
        laptop.status = Laptop.Status.DRAFT
        laptop.save()
        activity.log("laptop.duplicated", f"{source_sku} duplicated as {laptop.sku}", laptop=laptop,
                     object_type="laptop", object_id=laptop.pk, details={"source_sku": source_sku})
    modeladmin.message_user(request, "Duplicated as drafts. Add the serial number and photos for each.")


@admin.action(description="Export selected to CSV")
def export_csv(modeladmin, request, queryset):
    activity.log("laptop.exported", f"{queryset.count()} laptops exported to CSV",
                 details={"skus": list(queryset.values_list("sku", flat=True)[:100])})
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="laptops.csv"'
    writer = csv.writer(response)
    writer.writerow(["SKU", "Brand", "Model", "CPU", "GPU", "RAM GB", "Storage GB", "Storage type",
                     "Screen in", "Condition", "Serial", "Supplier", "Purchase cost", "Price", "Status"])
    for l in queryset.select_related("brand", "supplier"):
        writer.writerow([l.sku, l.brand, l.model, l.cpu, l.gpu, l.ram_gb, l.storage_gb, l.storage_type,
                         l.screen_inches, l.condition, l.serial_number or "", l.supplier or "",
                         l.purchase_cost or "", l.price, l.status])
    return response


@admin.register(Laptop)
class LaptopAdmin(AuditedAdminMixin, admin.ModelAdmin):
    change_form_template = "admin/inventory/laptop/change_form.html"
    inlines = [LaptopMediaInline]
    list_display = ["thumb", "sku", "title", "price", "status", "ram_gb", "storage_gb", "photo_count", "video_count"]
    list_display_links = ["thumb", "sku", "title"]
    list_filter = ["status", "brand", "ram_gb", "storage_type", "supplier"]
    search_fields = ["sku", "title", "model", "serial_number", "cpu"]
    list_per_page = 25
    actions = [mark_available, mark_draft, mark_investigation, mark_returned, duplicate_laptops, export_csv]
    readonly_fields = ["sku", "profit_display", "created_at", "updated_at"]
    autocomplete_fields = ["brand"]
    fieldsets = [
        ("Identity", {"fields": ["sku", "brand", "model", "title", "description", "status"]}),
        ("Specifications", {"fields": [
            "cpu", "gpu", "tpu", "ram_gb", ("storage_gb", "storage_type"),
            "screen_inches", "resolution", "os", "battery_health_pct", "keyboard_layout"]}),
        ("Condition and tracking", {"fields": [
            "condition", "defects_notes", "serial_number", "warranty_months", "supplier"]}),
        ("Pricing (INR)", {"fields": ["price", "purchase_cost", "profit_display"]}),
        ("Record", {"classes": ["collapse"], "fields": ["created_at", "updated_at"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("brand").prefetch_related("media")

    @admin.display(description="Photo")
    def thumb(self, obj):
        photos = [m for m in obj.media.all() if m.kind == "image" and m.thumbnail]
        if not photos:
            return "-"
        photos.sort(key=lambda m: (not m.is_primary, m.sort_order, m.id))
        return format_html('<img src="{}" style="width:64px;height:48px;object-fit:contain;background:#fff;border:1px solid #ddd;border-radius:4px">', photos[0].thumbnail.url)

    @admin.display(description="Photos")
    def photo_count(self, obj):
        return sum(1 for m in obj.media.all() if m.kind == "image")

    @admin.display(description="Videos")
    def video_count(self, obj):
        return sum(1 for m in obj.media.all() if m.kind == "video")

    @admin.display(description="Profit (INR)")
    def profit_display(self, obj):
        return obj.profit if obj.pk and obj.profit is not None else "-"

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        laptop = form.instance
        if laptop.status == Laptop.Status.AVAILABLE and not laptop.has_photo:
            set_status(Laptop.objects.filter(pk=laptop.pk), Laptop.Status.DRAFT, "no photo, cannot be available")
            self.message_user(
                request,
                "A laptop needs at least one photo before it can be Available. It was saved as Draft.",
                messages.WARNING,
            )

    def get_urls(self):
        custom = [
            path("<int:object_id>/upload-media/",
                 self.admin_site.admin_view(self.upload_media_view),
                 name="inventory_laptop_upload_media"),
        ]
        return custom + super().get_urls()

    def upload_media_view(self, request, object_id):
        laptop = get_object_or_404(Laptop, pk=object_id)
        if not self.has_change_permission(request, laptop):
            return redirect("admin:index")
        change_url = reverse("admin:inventory_laptop_change", args=[laptop.pk])
        if request.method == "POST":
            form = BulkMediaUploadForm(request.POST, request.FILES)
            if form.is_valid():
                files = request.FILES.getlist("files")
                had_primary = laptop.media.filter(is_primary=True, kind="image").exists()
                next_order = laptop.media.count()
                for f in files:
                    media = LaptopMedia(laptop=laptop, file=f, sort_order=next_order)
                    next_order += 1
                    if not had_primary and f.name.lower().rsplit(".", 1)[-1] not in ("mp4",):
                        media.is_primary = True
                        had_primary = True
                    media.save()
                activity.log("media.uploaded", f"{len(files)} file(s) uploaded for {laptop.sku}", laptop=laptop,
                             object_type="laptop", object_id=laptop.pk,
                             details={"files": [{"name": f.name, "size": f.size} for f in files]})
                self.message_user(request, f"{len(files)} file(s) uploaded.")
                return redirect(change_url)
            activity.log("media.rejected", f"Upload for {laptop.sku} rejected", level="WARNING", laptop=laptop,
                         details={"errors": form.errors.get("files", [])})
        else:
            form = BulkMediaUploadForm()
        context = {
            **self.admin_site.each_context(request),
            "title": f"Upload photos and videos: {laptop.sku}",
            "laptop": laptop,
            "form": form,
            "opts": self.model._meta,
            "change_url": change_url,
            "shot_list": SHOT_LIST,
        }
        return TemplateResponse(request, "admin/inventory/laptop/upload_media.html", context)
