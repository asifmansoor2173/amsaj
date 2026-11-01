"""Image processing helpers: resize large photos and create thumbnails."""
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

try:  # iPhone photos (HEIC) support
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

MAIN_MAX_PX = 1600
THUMB_MAX_PX = 400


def _to_webp(img, max_px, quality):
    img = img.copy()
    img.thumbnail((max_px, max_px))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buffer = BytesIO()
    img.save(buffer, format="WEBP", quality=quality)
    return ContentFile(buffer.getvalue())


def process_image(fileobj):
    """Return (main, thumbnail) as WebP ContentFiles. Fixes phone rotation."""
    fileobj.seek(0)
    img = Image.open(fileobj)
    img = ImageOps.exif_transpose(img)
    return _to_webp(img, MAIN_MAX_PX, 82), _to_webp(img, THUMB_MAX_PX, 75)
