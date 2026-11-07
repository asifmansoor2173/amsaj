from django.core.management.base import BaseCommand

from orders.models import PickupLocation, ShippingZone


class Command(BaseCommand):
    help = "Create a starter shipping zone and pickup location to edit in the admin (safe to run twice)."

    def handle(self, *args, **options):
        if not ShippingZone.objects.exists():
            ShippingZone.objects.create(
                name="Rest of India (edit me)", fee=0, eta="Set the estimated delivery time",
                schedule="", is_default=True)
            self.stdout.write("Created a default shipping zone. Set its fee in the admin.")
        if not PickupLocation.objects.exists():
            PickupLocation.objects.create(
                name="Store pickup", address="Set the pickup address in the admin", schedule="Set the pickup schedule")
            self.stdout.write("Created a pickup location. Set its address in the admin.")
        self.stdout.write(self.style.SUCCESS("Done."))
