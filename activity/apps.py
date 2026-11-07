from django.apps import AppConfig


class ActivityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "activity"
    verbose_name = "Activity log"

    def ready(self):
        from . import signals  # noqa: F401  (connects the sign-in and error handlers)
