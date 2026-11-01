import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from inventory.models import Brand, Laptop, LaptopMedia
from inventory.tests import make_image

from .templatetags.store_extras import inr

TEMP_MEDIA = tempfile.mkdtemp()


def add_laptop(brand_name="Dell", model="Latitude 7490", status="available", photo=True, **kwargs):
    brand, _ = Brand.objects.get_or_create(name=brand_name)
    data = dict(brand=brand, model=model, cpu="Core i5-8350U", ram_gb=8, storage_gb=256,
                storage_type="SSD", screen_inches="14.0", condition="Light scratches",
                price="25000", status=status)
    data.update(kwargs)
    laptop = Laptop.objects.create(**data)
    if photo:
        LaptopMedia.objects.create(laptop=laptop, file=make_image(), is_primary=True)
    return laptop


class InrFilterTests(TestCase):
    def test_indian_grouping(self):
        self.assertEqual(inr(999), "₹999")
        self.assertEqual(inr(25000), "₹25,000")
        self.assertEqual(inr(125000), "₹1,25,000")
        self.assertEqual(inr("10000000"), "₹1,00,00,000")
        self.assertEqual(inr(None), "")


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class StorefrontTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_health(self):
        self.assertEqual(self.client.get(reverse("health")).content, b"ok")

    def test_home_empty_and_with_stock(self):
        self.assertContains(self.client.get("/"), "No laptops are listed")
        add_laptop()
        response = self.client.get("/")
        self.assertContains(response, "Latitude 7490")
        self.assertContains(response, "₹25,000")

    def test_only_published_laptops_are_listed(self):
        add_laptop(model="Visible")
        add_laptop(model="Hidden draft", status="draft")
        add_laptop(model="Under check", status="under_investigation")
        add_laptop(model="Gone", status="sold")
        add_laptop(model="Held", status="reserved")
        response = self.client.get(reverse("laptop_list"))
        self.assertContains(response, "Visible")
        self.assertContains(response, "Held")
        self.assertNotContains(response, "Hidden draft")
        self.assertNotContains(response, "Under check")
        self.assertNotContains(response, "Gone")

    def test_filters_and_sorting(self):
        add_laptop("Dell", "Cheap Dell", price="20000", ram_gb=8)
        add_laptop("HP", "Pricey HP", price="60000", ram_gb=16, storage_type="NVMe", storage_gb=512, screen_inches="15.6")
        url = reverse("laptop_list")
        self.assertContains(self.client.get(url, {"brand": Brand.objects.get(name="HP").id}), "Pricey HP")
        self.assertNotContains(self.client.get(url, {"brand": Brand.objects.get(name="HP").id}), "Cheap Dell")
        self.assertNotContains(self.client.get(url, {"min_price": "30000"}), "Cheap Dell")
        self.assertNotContains(self.client.get(url, {"max_price": "30000"}), "Pricey HP")
        self.assertContains(self.client.get(url, {"ram": "16"}), "Pricey HP")
        self.assertNotContains(self.client.get(url, {"ram": "16"}), "Cheap Dell")
        self.assertContains(self.client.get(url, {"storage_type": "NVMe"}), "Pricey HP")
        self.assertNotContains(self.client.get(url, {"min_storage": "512"}), "Cheap Dell")
        self.assertContains(self.client.get(url, {"screen": "15.6"}), "Pricey HP")
        self.assertNotContains(self.client.get(url, {"screen": "15.6"}), "Cheap Dell")
        response = self.client.get(url, {"sort": "price_desc"})
        names = [l.model for l in response.context["page"].object_list]
        self.assertEqual(names, ["Pricey HP", "Cheap Dell"])

    def test_search(self):
        add_laptop("Lenovo", "ThinkPad T480")
        add_laptop("Dell", "Latitude 7490")
        response = self.client.get(reverse("laptop_list"), {"q": "thinkpad"})
        self.assertContains(response, "ThinkPad T480")
        self.assertNotContains(response, "Latitude 7490")

    def test_bad_filter_values_do_not_crash(self):
        add_laptop()
        response = self.client.get(reverse("laptop_list"),
                                   {"min_price": "abc", "ram": "x", "brand": "zz", "sort": "bogus", "page": "999"})
        self.assertEqual(response.status_code, 200)

    def test_pagination(self):
        for i in range(14):
            add_laptop(model=f"Model {i}", photo=False)
        response = self.client.get(reverse("laptop_list"))
        self.assertEqual(len(response.context["page"].object_list), 12)
        self.assertContains(response, "Page 1 of 2")
        self.assertEqual(len(self.client.get(reverse("laptop_list"), {"page": 2}).context["page"].object_list), 2)

    def test_detail_page(self):
        laptop = add_laptop(condition="Small dent on the corner", battery_health_pct=87)
        response = self.client.get(reverse("laptop_detail", args=[laptop.pk]))
        self.assertContains(response, "Only 1 available")
        self.assertContains(response, "Small dent on the corner")
        self.assertContains(response, "87%")
        self.assertContains(response, "2-month warranty")

    def test_detail_states(self):
        sold = add_laptop(status="sold")
        reserved = add_laptop(model="Held", status="reserved")
        draft = add_laptop(status="draft")
        returned = add_laptop(status="returned")
        self.assertContains(self.client.get(reverse("laptop_detail", args=[sold.pk])), "Sold")
        self.assertContains(self.client.get(reverse("laptop_detail", args=[reserved.pk])), "Reserved")
        self.assertEqual(self.client.get(reverse("laptop_detail", args=[draft.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("laptop_detail", args=[returned.pk])).status_code, 404)

    def test_returns_page(self):
        self.assertContains(self.client.get(reverse("returns_warranty")), "7 days")
