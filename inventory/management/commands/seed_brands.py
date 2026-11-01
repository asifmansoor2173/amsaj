from django.core.management.base import BaseCommand

from inventory.models import Brand

BRANDS = ["Dell", "HP", "Lenovo", "Apple", "Asus", "Acer", "MSI", "Microsoft", "Samsung", "Toshiba"]


class Command(BaseCommand):
    help = "Add the common laptop brands (safe to run more than once)."

    def handle(self, *args, **options):
        created = sum(Brand.objects.get_or_create(name=n)[1] for n in BRANDS)
        self.stdout.write(self.style.SUCCESS(f"{created} brand(s) added."))
