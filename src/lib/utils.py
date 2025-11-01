"""Miscellaneous utility functions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


# ------------------------------------------------------------------------------
def round_half_up(n):
    """Round to nearest integer (not bankers rounding)."""

    return int(Decimal(n).to_integral_value(rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------------------
def format_isodate_difference(iso1: str, iso2: str) -> str:
    """Format time difference in human readable format between two ISO formatted dates."""

    d1 = datetime.fromisoformat(iso1)
    d2 = datetime.fromisoformat(iso2)
    if not all((d1.tzinfo, d2.tzinfo)):
        raise ValueError('Arguments must be timezone-aware')
    if d1 > d2:
        sign = '-'
        d1, d2 = d2, d1
    else:
        sign = ''

    diff_seconds = (d2 - d1).total_seconds()

    if diff_seconds < 60:
        seconds = round_half_up(diff_seconds)
        return f'{sign}{seconds}s'

    if diff_seconds < 3600:
        minutes = int(diff_seconds // 60)
        seconds = round_half_up(diff_seconds % 60)
        if seconds == 60:
            minutes += 1
            seconds = 0
        return f'{sign}{minutes}m {seconds}s'

    hours = int(diff_seconds // 3600)
    remaining_seconds = diff_seconds % 3600
    minutes = round_half_up(remaining_seconds / 60)
    if minutes == 60:
        hours += 1
        minutes = 0
    return f'{sign}{hours}h {minutes}m'


# ------------------------------------------------------------------------------
def suppress_exception(
    func: Callable,
    *args,
    exc: type(Exception) | tuple[type(Exception)] = Exception,
    exc_return=None,
    **kwargs,
) -> Any:
    """
    Execute a function and suppress the specified exception(s).

    :param func:        The function (or any callable) to execute.
    :param args:        Function positional arguments.
    :param kwargs:      Function keyword arguments.
    :param exc:         An exception type or tuple of exception types to suppress.
    :param exc_return:  The value to return in the event of an exception.
    :return:            The return value of the supplied function or exc_return
                        in the event of an exception.
    """

    try:
        return func(*args, **kwargs)
    except exc:
        return exc_return
