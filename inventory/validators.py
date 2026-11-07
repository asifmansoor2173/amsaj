import os

from django.core.exceptions import ValidationError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4"}
MAX_IMAGE_MB = 10
MAX_VIDEO_MB = 100


def media_kind(filename):
    """Return 'image' or 'video' based on the file extension, else None."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def validate_media_file(f):
    # Files that are already saved were checked when they were uploaded. Skipping them
    # means editing a laptop still works even if a stored file is missing from disk.
    if getattr(f, "_committed", False):
        return
    kind = media_kind(f.name)
    if kind is None:
        raise ValidationError(
            "%(name)s: unsupported file type. Use JPG, PNG, WebP, HEIC for photos or MP4 for videos.",
            params={"name": f.name},
        )
    limit_mb = MAX_IMAGE_MB if kind == "image" else MAX_VIDEO_MB
    if f.size > limit_mb * 1024 * 1024:
        raise ValidationError(
            "%(name)s is too large. Maximum for a %(kind)s is %(mb)s MB.",
            params={"name": f.name, "kind": kind, "mb": limit_mb},
        )
