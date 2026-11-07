"""One function for changing laptop status, so every change is logged with the reason."""
from activity import logger as activity

from .models import Laptop


def set_status(queryset, new_status, reason, order=None, actor_type=None, actor_name=None):
    """Change the status of the laptops in `queryset`, log each change, return how many changed."""
    rows = list(queryset.exclude(status=new_status).values_list("pk", "sku", "status"))
    if not rows:
        return 0
    Laptop.objects.filter(pk__in=[r[0] for r in rows]).update(status=new_status)
    for pk, sku, old in rows:
        activity.log("laptop.status_changed", f"{sku}: {old} -> {new_status} ({reason})", order=order,
                     laptop_sku=sku, object_type="laptop", object_id=pk,
                     actor_type=actor_type, actor_name=actor_name,
                     details={"from": old, "to": new_status, "reason": reason})
    return len(rows)
