from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from activity.models import ActivityLog


class Command(BaseCommand):
    help = "Delete activity log rows older than N days. Not run automatically: by default nothing is ever deleted."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, required=True)

    def handle(self, *args, days, **options):
        cutoff = timezone.now() - timedelta(days=days)
        count, _ = ActivityLog.objects.filter(occurred_at__lt=cutoff).delete()
        self.stdout.write(f"{count} row(s) older than {days} days deleted.")
