import sys
import traceback

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.core.signals import got_request_exception
from django.dispatch import receiver

from . import logger


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    logger.log("auth.login", f"{user.get_username()} signed in", actor_type="staff" if user.is_staff else "customer",
               actor_name=user.get_username(), object_type="user", object_id=user.pk)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user:
        logger.log("auth.logout", f"{user.get_username()} signed out", actor_type="staff" if user.is_staff else "customer",
                   actor_name=user.get_username())


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    logger.log("auth.login_failed", "Failed sign-in attempt", level="WARNING",
               details={"username": credentials.get("username", "")})


@receiver(got_request_exception)
def on_exception(sender, request=None, **kwargs):
    exc_type, exc, tb = sys.exc_info()
    logger.log("system.error", f"{exc_type.__name__ if exc_type else 'Error'}: {exc}"[:400], level="ERROR",
               details={"traceback": "".join(traceback.format_exception(exc_type, exc, tb))[-3000:]})
