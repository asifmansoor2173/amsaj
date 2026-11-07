import time

from django.conf import settings

from . import logger

SKIP_PREFIXES = ("/static/", "/media/", "/health/", "/favicon.ico", "/admin/jsi18n/")


class ActivityMiddleware:
    """Starts the per-request context and records one 'web.request' row for each page request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ctx, token = logger.start_request(request)
        try:
            response = self.get_response(request)
            if settings.ACTIVITY_LOG_REQUESTS and not request.path.startswith(SKIP_PREFIXES):
                status = response.status_code
                query = {k: (v if len(v) > 1 else v[0]) for k, v in request.GET.lists()}
                logger.log(
                    "web.request", f"{request.method} {ctx.path} -> {status}",
                    level="ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO",
                    status_code=status, duration_ms=int((time.monotonic() - ctx.started) * 1000),
                    details={"query": query} if query else {},
                )
            return response
        finally:
            logger.end_request(token)
