from django.core.management.base import BaseCommand

from orders.services import release_expired


class Command(BaseCommand):
    help = "Cancel unpaid orders whose reservation expired and put the laptops back on sale."

    def handle(self, *args, **options):
        self.stdout.write(f"{release_expired()} expired order(s) released.")
