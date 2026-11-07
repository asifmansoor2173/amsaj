from . import logger


def _refs(obj):
    refs = {}
    if hasattr(obj, "order_no") and getattr(obj, "order_no", ""):
        refs["order_no_value"] = obj.order_no
    if hasattr(obj, "sku") and getattr(obj, "sku", ""):
        refs["laptop_sku"] = obj.sku
    return refs


def _log_kwargs(obj):
    kwargs = {"object_type": obj._meta.model_name, "object_id": obj.pk if obj.pk is not None else ""}
    refs = _refs(obj)
    if "laptop_sku" in refs:
        kwargs["laptop_sku"] = refs["laptop_sku"]
    return kwargs, refs.get("order_no_value", "")


class AuditedAdminMixin:
    """Add to any ModelAdmin: every create, change and delete by staff is logged with the old and new values."""

    def save_model(self, request, obj, form, change):
        changes = {}
        if change:
            for name in form.changed_data:
                changes[name] = {"from": str(form.initial.get(name, "")), "to": str(form.cleaned_data.get(name, ""))}
        super().save_model(request, obj, form, change)
        kwargs, order_no = _log_kwargs(obj)
        logger.log(("admin.updated" if change else "admin.created"),
                   f"{obj._meta.verbose_name} {'updated' if change else 'created'}: {obj}"[:400],
                   details={"changes": changes} if change else {"fields": list(form.cleaned_data.keys())},
                   **kwargs)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        added = [str(o) for o in instances if o.pk is None]
        changed = [str(o) for o, _fields in formset.changed_objects]
        deleted = [str(o) for o in formset.deleted_objects]
        super().save_formset(request, form, formset, change)
        if added or changed or deleted:
            kwargs, _ = _log_kwargs(form.instance)
            logger.log("admin.inline_changed", f"{formset.model._meta.verbose_name_plural} changed on {form.instance}"[:400],
                       details={"added": added, "changed": changed, "deleted": deleted}, **kwargs)

    def delete_model(self, request, obj):
        kwargs, _ = _log_kwargs(obj)
        label = str(obj)
        super().delete_model(request, obj)
        logger.log("admin.deleted", f"{obj._meta.verbose_name} deleted: {label}"[:400], level="WARNING", **kwargs)

    def delete_queryset(self, request, queryset):
        labels = [str(o) for o in queryset[:50]]
        count = queryset.count()
        super().delete_queryset(request, queryset)
        logger.log("admin.deleted", f"{count} {queryset.model._meta.verbose_name_plural} deleted", level="WARNING",
                   details={"items": labels})
