"""Remembering an answer for as long as that answer is worth remembering.

Streamlit's own cache keeps whatever a function returned for the whole of its ttl, and for a
feed that is the wrong rule. A reading that arrived is good for an hour. The silence from a
host that blinked is good for about two minutes — but kept for the same hour it reads as a
dead panel long after the host has come back, and the reader has no way to tell the two apart.

This is not hypothetical. PortWatch refuses about one query in four, measured over eight
tries; with a three-hour ttl a single refusal meant three hours of "not answering". FRED went
one worse: a momentary timeout was cached as an empty series for an hour, for every series at
once, so the whole money side of the page went blank over a blip.
"""
from __future__ import annotations

import functools
import time


def empty(value) -> bool:
    """Whether a feed came back with nothing.

    A tuple is judged by its first element: the modules here return (rows, why) when they want
    to explain themselves, and it is the rows that decide whether the answer was worth having.
    """
    if isinstance(value, tuple) and value:
        value = value[0]
    if value is None:
        return True
    try:
        return len(value) == 0
    except TypeError:          # a number, a bool: it arrived, so it counts
        return False


def remember(good: int, bad: int = 120):
    """Hold a real answer for `good` seconds and an empty one for `bad`.

    Per process and per argument list, like the cache it replaces. Nothing is written to disk:
    these are all small, and a feed worth re-reading is cheaper to fetch than to serialise.
    """
    def wrap(fn):
        held: dict = {}

        @functools.wraps(fn)
        def call(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            hit = held.get(key)
            if hit and now < hit[0]:
                return hit[1]
            value = fn(*args, **kwargs)
            held[key] = (now + (bad if empty(value) else good), value)
            return value

        call.forget = held.clear
        return call
    return wrap
