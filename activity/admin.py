import csv
import json

from django.contrib import admin
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import ActivityLog


@admin.action(description="Export selected to CSV")
def export_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="activity_log.csv"'
    writer = csv.writer(response)
    writer.writerow(["Time", "Transaction", "Event", "Level", "Action", "Message", "Actor type", "Actor",
                     "IP", "Method", "Path", "Status", "ms", "Order", "Laptop", "Details"])
    for e in queryset.order_by("occurred_at", "id"):
        writer.writerow([e.occurred_at.isoformat(), e.transaction_id, e.event_id, e.level, e.action, e.message,
                         e.actor_type, e.actor_name, e.ip_address or "", e.http_method, e.path,
                         e.status_code or "", e.duration_ms or "", e.order_no, e.laptop_sku,
                         json.dumps(e.details, ensure_ascii=False)])
    return response


def trace_events(key):
    """Every row connected to a transaction id, order number, laptop code, event id or request id."""
    key = key.strip()
    base = ActivityLog.objects.filter(Q(transaction_id=key) | Q(order_no=key) | Q(laptop_sku=key)
                                      | Q(event_id=key) | Q(request_id=key))
    txns = set(base.values_list("transaction_id", flat=True))
    return ActivityLog.objects.filter(Q(transaction_id__in=txns) | Q(pk__in=base.values("pk"))).order_by("occurred_at", "id")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    """Read-only. Nobody can add, change or delete entries from here."""

    list_display = ["occurred_at", "level", "category", "action", "trace_link", "order_no", "laptop_sku",
                    "actor_type", "actor_name", "short_message", "status_code"]
    list_filter = ["level", "category", "actor_type", "action", ("occurred_at", admin.DateFieldListFilter)]
    search_fields = ["transaction_id", "event_id", "request_id", "order_no", "laptop_sku", "actor_name",
                     "message", "path", "ip_address"]
    date_hierarchy = "occurred_at"
    list_per_page = 50
    actions = [export_csv]
    change_list_template = "admin/activity/activitylog/change_list.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Transaction (click to trace)", ordering="transaction_id")
    def trace_link(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:activity_activitylog_trace", args=[obj.transaction_id]),
                           obj.transaction_id)

    @admin.display(description="Message")
    def short_message(self, obj):
        return obj.message[:90]

    def get_urls(self):
        custom = [
            path("trace/", self.admin_site.admin_view(self.trace_search), name="activity_activitylog_trace_search"),
            path("trace/<str:key>/", self.admin_site.admin_view(self.trace), name="activity_activitylog_trace"),
        ]
        return custom + super().get_urls()

    def trace_search(self, request):
        key = request.GET.get("q", "").strip()
        if key:
            return redirect("admin:activity_activitylog_trace", key=key)
        return self.trace(request, "")

    def trace(self, request, key):
        events = list(trace_events(key)) if key else []
        groups = []
        for event in events:
            if not groups or groups[-1]["transaction_id"] != event.transaction_id:
                groups.append({"transaction_id": event.transaction_id, "events": []})
            groups[-1]["events"].append({"obj": event, "details": json.dumps(event.details, indent=2, ensure_ascii=False)
                                         if event.details else ""})
        context = {**self.admin_site.each_context(request), "title": "Activity trace", "key": key, "groups": groups,
                   "count": len(events), "opts": self.model._meta}
        return render(request, "admin/activity/trace.html", context)
