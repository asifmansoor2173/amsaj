from django.core.management.base import BaseCommand

from activity import logger as activity
from inventory.media_utils import MAIN_SIZE, THUMB_SIZE, image_size, process_image
from inventory.models import LaptopMedia


class Command(BaseCommand):
    help = ("Make every stored laptop photo the same size (1200x900, thumbnails 400x300). "
            "Photos that are already the right size are skipped, so it is safe to run again.")

    def handle(self, *args, **options):
        done = skipped = missing = 0
        for media in LaptopMedia.objects.filter(kind="image").select_related("laptop"):
            main_size = image_size(media.file) if media.file else None
            thumb_size = image_size(media.thumbnail) if media.thumbnail else None
            if main_size is None:
                missing += 1
                self.stdout.write(self.style.WARNING(f"Missing file for {media} (id {media.pk}); skipped."))
                continue
            if main_size == MAIN_SIZE and thumb_size == THUMB_SIZE:
                skipped += 1
                continue
            old_names = [media.file.name, media.thumbnail.name]
            with media.file.open("rb") as handle:
                main, thumb = process_image(handle)
            media.file.save("photo.webp", main, save=False)
            media.thumbnail.save("thumb.webp", thumb, save=False)
            LaptopMedia.objects.filter(pk=media.pk).update(file=media.file.name, thumbnail=media.thumbnail.name)
            for name in old_names:
                if name and name not in (media.file.name, media.thumbnail.name):
                    media.file.storage.delete(name)
            activity.log("media.regenerated", f"Photo resized to a uniform frame for {media.laptop.sku}",
                         laptop_sku=media.laptop.sku, object_type="laptopmedia", object_id=media.pk,
                         details={"from": list(main_size), "to": list(MAIN_SIZE)})
            done += 1
        self.stdout.write(self.style.SUCCESS(f"{done} photo(s) resized, {skipped} already uniform, {missing} missing."))
