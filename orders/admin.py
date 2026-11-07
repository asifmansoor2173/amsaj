from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from . import services
from .models import Order, OrderItem, Payment, PickupLocation, ReturnCase, Shipment, ShippingZone
from .services import OrderError


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ["sku", "title", "price", "laptop"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    fields = ["created_at", "method", "gateway", "transaction_id", "amount", "status", "note"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ShipmentInline(admin.StackedInline):
    model = Shipment
    extra = 1
    max_num = 1
    can_delete = False
    fields = ["courier", "tracking_no", "planned_date", "shipped_at", "delivered_at"]
    readonly_fields = ["shipped_at", "delivered_at"]


def _bulk_action(key):
    label, func = services.ACTIONS[key]

    @admin.action(description=label)
    def action(modeladmin, request, queryset):
        done = 0
        for order in queryset:
            try:
                func(order)
                done += 1
            except OrderError as error:
                modeladmin.message_user(request, f"{order.order_no}: {error}", messages.WARNING)
        if done:
            modeladmin.message_user(request, f"{label}: {done} order(s) updated.")

    action.__name__ = f"do_{key}"
    return action


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    change_form_template = "admin/orders/order/change_form.html"
    inlines = [OrderItemInline, PaymentInline, ShipmentInline]
    list_display = ["order_no", "created_at", "customer_name", "phone", "total", "payment_method",
                    "payment_status", "status", "delivery_method"]
    list_filter = ["status", "payment_status", "payment_method", "delivery_method"]
    search_fields = ["order_no", "customer_name", "email", "phone", "bank_reference"]
    date_hierarchy = "created_at"
    actions = [_bulk_action(k) for k in ("confirm_payment", "confirm_cod", "processing", "shipped",
                                         "delivered", "cancel", "refunded")]
    fieldsets = [
        ("Order", {"fields": ["order_no", "status", "payment_status", "payment_method", "created_at",
                              "confirmed_at", "expires_at", "cancel_reason"]}),
        ("Customer", {"fields": ["customer_name", "email", "phone", "customer_notes"]}),
        ("Delivery", {"fields": ["delivery_method", "address_line1", "address_line2", "city", "state",
                                 "pin_code", "shipping_zone", "pickup_location"]}),
        ("Amounts (INR)", {"fields": ["subtotal", "shipping_fee", "total", "bank_reference"]}),
        ("Staff notes", {"fields": ["staff_notes"]}),
    ]
    # Everything is read-only except the bank reference and staff notes. Status changes go
    # through the buttons, so the stock rules (reserved, sold, available) are always applied.
    readonly_fields = [
        "order_no", "status", "payment_status", "payment_method", "created_at", "confirmed_at", "expires_at",
        "cancel_reason", "customer_name", "email", "phone", "customer_notes", "delivery_method",
        "address_line1", "address_line2", "city", "state", "pin_code", "shipping_zone", "pickup_location",
        "subtotal", "shipping_fee", "total",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom = [
            path("<int:object_id>/action/<str:key>/", self.admin_site.admin_view(self.run_action),
                 name="orders_order_action"),
            path("<int:object_id>/invoice/", self.admin_site.admin_view(self.invoice),
                 name="orders_order_invoice"),
        ]
        return custom + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        order = Order.objects.filter(pk=object_id).first()
        if order:
            extra_context["order_actions"] = services.available_actions(order)
            extra_context["invoice_url"] = reverse("admin:orders_order_invoice", args=[order.pk])
        return super().change_view(request, object_id, form_url, extra_context)

    @method_decorator(require_POST)
    def run_action(self, request, object_id, key):
        order = get_object_or_404(Order, pk=object_id)
        change_url = reverse("admin:orders_order_change", args=[order.pk])
        if key not in dict(services.available_actions(order)):
            self.message_user(request, "That action is not available for this order right now.", messages.ERROR)
            return redirect(change_url)
        try:
            services.ACTIONS[key][1](order)
            self.message_user(request, f"Done: {services.ACTIONS[key][0]}.")
        except OrderError as error:
            self.message_user(request, str(error), messages.ERROR)
        return redirect(change_url)

    def invoice(self, request, object_id):
        order = get_object_or_404(Order.objects.prefetch_related("items"), pk=object_id)
        return render(request, "orders/invoice.html", {"order": order})


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ["name", "fee", "eta", "schedule", "is_default", "active"]


@admin.register(PickupLocation)
class PickupLocationAdmin(admin.ModelAdmin):
    list_display = ["name", "schedule", "active"]


@admin.action(description="Fixed: put the laptop back in stock (Available)")
def return_fixed(modeladmin, request, queryset):
    for case in queryset:
        try:
            services.close_return(case, fixed=True)
        except OrderError as error:
            modeladmin.message_user(request, str(error), messages.WARNING)


@admin.action(description="Not fixable: send the laptop back to the supplier (Returned)")
def return_to_supplier(modeladmin, request, queryset):
    for case in queryset:
        try:
            services.close_return(case, fixed=False)
        except OrderError as error:
            modeladmin.message_user(request, str(error), messages.WARNING)


@admin.register(ReturnCase)
class ReturnCaseAdmin(admin.ModelAdmin):
    list_display = ["order_item", "outcome", "opened_at", "closed_at"]
    list_filter = ["outcome"]
    actions = [return_fixed, return_to_supplier]
    fields = ["order_item", "reason", "notes", "outcome", "opened_at", "closed_at"]

    def get_readonly_fields(self, request, obj=None):
        base = ["outcome", "opened_at", "closed_at"]
        return base + ["order_item"] if obj else base

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "order_item":
            kwargs["queryset"] = OrderItem.objects.filter(
                order__status__in=[Order.Status.CONFIRMED, Order.Status.PROCESSING,
                                   Order.Status.SHIPPED, Order.Status.DELIVERED]).select_related("order")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            services.mark_under_investigation(obj)
            self.message_user(request, "The laptop is now Under investigation. Use the actions on the list when the case is resolved.")
