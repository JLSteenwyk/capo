"""Normalize retry and access signals from connected-service HTTP responses."""
import math
import time
from email.utils import parsedate_to_datetime

from .providers import ServiceAuthenticationError
from .recovery import RateLimited


def check(response, service):
    status = response.status_code
    if type(status) is not int:
        return
    if status == 401:
        raise ServiceAuthenticationError(service)
    if status == 403:
        raise PermissionError(service+' denied access to this action.')
    if status == 429:
        now = time.time()
        reset = now + 60
        value = response.headers.get('Retry-After', '')
        try:
            delay = float(value)
            if math.isfinite(delay):
                reset = now + max(0, delay)
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is not None:
                    reset = max(now, date.timestamp())
            except (ValueError, TypeError, OverflowError):
                pass
        raise RateLimited(reset)
    if status >= 500:
        raise ConnectionError(service+' is temporarily unavailable.')
