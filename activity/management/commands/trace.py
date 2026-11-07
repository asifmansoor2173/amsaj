from django.core.management.base import BaseCommand

from activity.admin import trace_events


class Command(BaseCommand):
    help = "Print the complete activity trail for an order number, transaction id, laptop code or event id."

    def add_arguments(self, parser):
        parser.add_argument("key", help="e.g. ORD-000012, TXN-ABC123DEF456 or AMS-000007")

    def handle(self, *args, key, **options):
        events = list(trace_events(key))
        if not events:
            self.stdout.write(f"No activity found for {key}.")
            return
        current = None
        for e in events:
            if e.transaction_id != current:
                current = e.transaction_id
                self.stdout.write(self.style.MIGRATE_HEADING(f"\nTransaction {current}"))
            who = f"{e.actor_type}{':' + e.actor_name if e.actor_name else ''}"
            self.stdout.write(f"{e.occurred_at:%Y-%m-%d %H:%M:%S}  {e.level:<7} {e.action:<28} {who:<24} {e.message}")
