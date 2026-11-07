import time

from django.conf import settings

from .services import release_expired

_last_run = 0.0


class ReleaseExpiredReservationsMiddleware:
    """Puts laptops from unpaid, expired orders back on sale. Runs at most once every 30 seconds,
    so no separate scheduled job is needed while you run locally."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _last_run
        if settings.RELEASE_EXPIRED_ON_REQUEST and not request.path.startswith(("/static/", "/media/")):
            now = time.monotonic()
            if now - _last_run > 30:
                _last_run = now
                release_expired()
        return self.get_response(request)
