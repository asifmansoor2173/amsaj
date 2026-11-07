import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Brand, Laptop, LaptopMedia

TEMP_MEDIA = tempfile.mkdtemp()


def make_image(name="photo.jpg", size=(3000, 2000), fmt="JPEG"):
    buf = BytesIO()
    Image.new("RGB", size, "blue").save(buf, format=fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


def make_laptop(**kwargs):
    brand, _ = Brand.objects.get_or_create(name="Dell")
    data = dict(brand=brand, model="Latitude 7490", cpu="Core i7-8650U", ram_gb=16,
                storage_gb=512, storage_type="SSD", screen_inches="14.0",
                condition="Light scratches on lid", price="35000")
    data.update(kwargs)
    return Laptop.objects.create(**data)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class InventoryTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(user)

    def test_sku_and_title_are_generated(self):
        laptop = make_laptop()
        self.assertTrue(laptop.sku.startswith("AMS-"))
        self.assertIn("Latitude 7490", laptop.title)
        self.assertEqual(laptop.status, Laptop.Status.DRAFT)

    def test_photo_is_resized_and_thumbnailed(self):
        laptop = make_laptop()
        media = LaptopMedia.objects.create(laptop=laptop, file=make_image())
        self.assertEqual(media.kind, "image")
        self.assertTrue(media.file.name.endswith(".webp"))
        self.assertTrue(media.thumbnail.name.endswith(".webp"))
        with Image.open(media.file.path) as img:
            self.assertLessEqual(max(img.size), 1600)

    def test_only_one_primary_photo(self):
        laptop = make_laptop()
        first = LaptopMedia.objects.create(laptop=laptop, file=make_image(), is_primary=True)
        second = LaptopMedia.objects.create(laptop=laptop, file=make_image(), is_primary=True)
        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)

    def test_bulk_upload_photos_and_video(self):
        laptop = make_laptop()
        url = reverse("admin:inventory_laptop_upload_media", args=[laptop.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        video = SimpleUploadedFile("demo.mp4", b"\x00\x00\x00\x18ftypmp42", content_type="video/mp4")
        response = self.client.post(url, {"files": [make_image("a.jpg"), make_image("b.jpg"), video]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(laptop.media.filter(kind="image").count(), 2)
        self.assertEqual(laptop.media.filter(kind="video").count(), 1)
        self.assertEqual(laptop.media.filter(is_primary=True).count(), 1)

    def test_bulk_upload_rejects_bad_file_type(self):
        laptop = make_laptop()
        url = reverse("admin:inventory_laptop_upload_media", args=[laptop.pk])
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        response = self.client.post(url, {"files": [bad]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unsupported file type")
        self.assertEqual(laptop.media.count(), 0)

    def test_available_requires_a_photo(self):
        laptop = make_laptop()
        self.client.post(reverse("admin:inventory_laptop_changelist"),
                         {"action": "mark_available", "_selected_action": [laptop.pk]})
        laptop.refresh_from_db()
        self.assertEqual(laptop.status, Laptop.Status.DRAFT)
        LaptopMedia.objects.create(laptop=laptop, file=make_image())
        self.client.post(reverse("admin:inventory_laptop_changelist"),
                         {"action": "mark_available", "_selected_action": [laptop.pk]})
        laptop.refresh_from_db()
        self.assertEqual(laptop.status, Laptop.Status.AVAILABLE)

    def test_duplicate_action_creates_draft_without_serial(self):
        laptop = make_laptop(serial_number="SN123", status="available")
        self.client.post(reverse("admin:inventory_laptop_changelist"),
                         {"action": "duplicate_laptops", "_selected_action": [laptop.pk]})
        self.assertEqual(Laptop.objects.count(), 2)
        copy = Laptop.objects.exclude(pk=laptop.pk).get()
        self.assertEqual(copy.status, "draft")
        self.assertIsNone(copy.serial_number)
        self.assertNotEqual(copy.sku, laptop.sku)

    def test_admin_pages_load(self):
        laptop = make_laptop()
        for name, args in [("admin:inventory_laptop_changelist", []),
                           ("admin:inventory_laptop_add", []),
                           ("admin:inventory_laptop_change", [laptop.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_csv_export(self):
        laptop = make_laptop()
        response = self.client.post(reverse("admin:inventory_laptop_changelist"),
                                    {"action": "export_csv", "_selected_action": [laptop.pk]})
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(laptop.sku, response.content.decode())

    def test_editing_laptop_works_when_a_stored_file_is_missing_from_disk(self):
        """Regression: a missing photo file on disk must not crash the laptop edit page."""
        import os
        laptop = make_laptop()
        media = LaptopMedia.objects.create(laptop=laptop, file=make_image(), is_primary=True)
        os.remove(media.file.path)
        url = reverse("admin:inventory_laptop_change", args=[laptop.pk])
        form = self.client.get(url).context["adminform"].form
        data = {k: (v if v is not None else "") for k, v in form.initial.items()}
        data.update({
            "brand": laptop.brand_id, "status": "draft", "price": "36000",
            "media-TOTAL_FORMS": 1, "media-INITIAL_FORMS": 1, "media-MIN_NUM_FORMS": 0,
            "media-MAX_NUM_FORMS": 1000, "media-0-id": media.pk, "media-0-laptop": laptop.pk,
            "media-0-alt_text": "Top view", "media-0-sort_order": 0, "media-0-is_primary": "on",
        })
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        laptop.refresh_from_db()
        self.assertEqual(str(laptop.price), "36000.00")
