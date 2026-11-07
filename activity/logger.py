"""The single place where activities are recorded.

    from activity import logger as activity
    activity.log("order.created", "Order placed", order=order, details={...})

Every call writes the SAME information to two places:
  1. the log file   logs/activity.log   (one JSON object per line)
  2. the database   table ActivityLog   (searchable in the admin)
A failure while logging is itself logged but never breaks the page or the order.
"""
import contextvars
import json
import logging
import re
import time
import uuid

from django.conf import settings
from django.utils import timezone

file_logger = logging.getLogger("amsaj.activity")
app_logger = logging.getLogger("amsaj.app")

SESSION_KEY = "txn"
JOURNEY_CATEGORIES = {"cart", "checkout", "order", "payment"}
SENSITIVE = re.compile(r"pass|token|secret|csrf|card|cvv|otp", re.I)
LEVELS = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}

_current = contextvars.ContextVar("activity_context", default=None)


def new_transaction_id():
    return "TXN-" + uuid.uuid4().hex[:12].upper()


# ---- masking: logs are for tracing, they must not leak secrets --------------

def mask_email(value):
    if not value or "@" not in value:
        return value or ""
    name, domain = value.split("@", 1)
    return f"{name[:1]}***@{domain}"


def mask_phone(value):
    digits = re.sub(r"\D", "", value or "")
    return ("*" * max(len(digits) - 4, 0)) + digits[-4:] if digits else ""


def mask_path(path):
    """Order pages and the test payment page contain a private link token. Never log it."""
    path = re.sub(r"^/orders/[^/]+/", "/orders/{token}/", path)
    return re.sub(r"^/pay/test/[^/]+/", "/pay/test/{token}/", path)


def clean_details(value):
    """Make details JSON-safe and drop anything that looks like a password or token."""
    if isinstance(value, dict):
        return {str(k): ("***" if SENSITIVE.search(str(k)) else clean_details(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean_details(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ---- per-request context ---------------------------------------------------

def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None


class RequestContext:
    def __init__(self, request):
        self.request = request
        self.request_id = uuid.uuid4().hex
        self.request_txn = new_transaction_id()
        self.txn_override = None
        self.ip = client_ip(request)
        self.user_agent = request.META.get("HTTP_USER_AGENT", "")[:200]
        self.method = request.method
        self.path = mask_path(request.path)[:300]
        self.started = time.monotonic()

    def user(self):
        user = getattr(self.request, "user", None)
        return user if user is not None and user.is_authenticated else None

    def is_staff(self):
        user = self.user()
        return bool(user and user.is_staff)

    def actor(self):
        user = self.user()
        if user:
            return ("staff" if user.is_staff else "customer"), user.get_username()
        return "visitor", ""

    def journey_id(self, create=False):
        """The visitor's current shopping flow id, kept in their session until an order is placed."""
        session = getattr(self.request, "session", None)
        if session is None or self.is_staff():
            return None
        txn = session.get(SESSION_KEY)
        if not txn and create:
            txn = new_transaction_id()
            session[SESSION_KEY] = txn
            session.modified = True
        return txn

    def end_journey(self):
        session = getattr(self.request, "session", None)
        if session is not None:
            session.pop(SESSION_KEY, None)


def start_request(request):
    ctx = RequestContext(request)
    return ctx, _current.set(ctx)


def end_request(token):
    _current.reset(token)


def context():
    return _current.get()


def journey_id():
    """Transaction id to give a new order, so its history starts at the first cart click."""
    ctx = context()
    return ctx.journey_id(create=True) if ctx else None


def end_journey():
    ctx = context()
    if ctx:
        ctx.end_journey()


def bind_order(order):
    """Make the rest of this web request part of the order's trace."""
    ctx = context()
    if ctx and order is not None and order.transaction_id:
        ctx.txn_override = order.transaction_id


# ---- the log call ----------------------------------------------------------

def _resolve_transaction(explicit, order, ctx, category):
    if explicit:
        return explicit
    if order is not None and getattr(order, "transaction_id", ""):
        return order.transaction_id
    if ctx is not None:
        if ctx.txn_override:
            return ctx.txn_override
        journey = ctx.journey_id(create=category in JOURNEY_CATEGORIES)
        return journey or ctx.request_txn
    return new_transaction_id()


def log(action, message="", *, level="INFO", order=None, laptop=None, laptop_sku="", object_type="",
        object_id="", details=None, transaction_id=None, actor_type=None, actor_name=None,
        status_code=None, duration_ms=None):
    try:
        ctx = context()
        category = action.split(".")[0]
        if laptop is not None:
            laptop_sku = laptop_sku or laptop.sku
        if ctx is not None:
            default_type, default_name = ctx.actor()
        else:
            default_type, default_name = "system", ""
        if order is not None and default_type == "visitor":
            default_type, default_name = "customer", mask_email(order.email)
        now = timezone.now()
        data = {
            "event_id": uuid.uuid4().hex,
            "timestamp": timezone.localtime(now).isoformat(),
            "transaction_id": _resolve_transaction(transaction_id, order, ctx, category),
            "request_id": ctx.request_id if ctx else "",
            "level": level,
            "category": category,
            "action": action,
            "message": (message or "")[:400],
            "actor_type": actor_type or default_type,
            "actor_name": (actor_name if actor_name is not None else default_name)[:150],
            "ip_address": ctx.ip if ctx else None,
            "user_agent": ctx.user_agent if ctx else "",
            "http_method": ctx.method if ctx else "",
            "path": ctx.path if ctx else "",
            "status_code": status_code,
            "duration_ms": duration_ms,
            "order_no": order.order_no if order is not None else "",
            "laptop_sku": laptop_sku or "",
            "object_type": object_type,
            "object_id": str(object_id) if object_id != "" else "",
            "details": clean_details(details or {}),
        }
        compact = (f"{data['transaction_id']} {action}"
                   f"{' order=' + data['order_no'] if data['order_no'] else ''}"
                   f"{' sku=' + data['laptop_sku'] if data['laptop_sku'] else ''}"
                   f" | {data['message']}")
        file_logger.log(LEVELS.get(level, logging.INFO), compact, extra={"record_data": data})
        _write_row(data, now)
        return data["transaction_id"]
    except Exception:  # logging must never break the shop
        app_logger.exception("Could not write activity log entry for %s", action)
        return None


def _write_row(data, now):
    from .models import ActivityLog
    ActivityLog.objects.create(
        event_id=data["event_id"], transaction_id=data["transaction_id"], request_id=data["request_id"],
        occurred_at=now, level=data["level"], category=data["category"], action=data["action"],
        message=data["message"], actor_type=data["actor_type"], actor_name=data["actor_name"],
        ip_address=data["ip_address"], user_agent=data["user_agent"], http_method=data["http_method"],
        path=data["path"], status_code=data["status_code"], duration_ms=data["duration_ms"],
        order_no=data["order_no"], laptop_sku=data["laptop_sku"], object_type=data["object_type"],
        object_id=data["object_id"], details=data["details"],
    )
