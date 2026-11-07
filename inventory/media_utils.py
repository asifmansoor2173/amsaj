"""Image processing: every laptop photo becomes exactly the same size.

Phones and cameras give photos of all shapes. Each photo is shrunk to fit inside a fixed 4:3
frame and centred on a white background, so nothing is cut off (ports, defects and so on stay
visible) and every picture in the shop and the admin has identical dimensions.
"""
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

try:  # iPhone photos (HEIC) support
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

MAIN_SIZE = (1200, 900)    # gallery photo on the product page
THUMB_SIZE = (400, 300)    # shop cards, cart and admin previews
BACKGROUND = (255, 255, 255)


def _to_rgb(img):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, BACKGROUND)
        flat.paste(img, mask=img.split()[-1])
        return flat
    return img.convert("RGB")


def _on_canvas(img, size, quality):
    img = _to_rgb(img)
    img.thumbnail(size, Image.LANCZOS)          # shrink only, never enlarge
    canvas = Image.new("RGB", size, BACKGROUND)
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    buffer = BytesIO()
    canvas.save(buffer, format="WEBP", quality=quality)
    return ContentFile(buffer.getvalue())


def process_image(fileobj):
    """Return (main, thumbnail) as WebP files of fixed size. Also fixes phone rotation."""
    fileobj.seek(0)
    img = ImageOps.exif_transpose(Image.open(fileobj))
    return _on_canvas(img, MAIN_SIZE, 85), _on_canvas(img, THUMB_SIZE, 78)


def image_size(fieldfile):
    """(width, height) of a stored image, or None if it cannot be read."""
    try:
        with fieldfile.open("rb") as handle:
            with Image.open(handle) as img:
                return img.size
    except (OSError, ValueError):
        return None
