"""Encode and decode component custom_ids.

Discord caps a custom_id at 100 characters and gives no error when one is
malformed - the interaction simply does nothing. Keeping the format in one
tested module is what stops that being discovered in production.

Format: px:<kind>:<arg>   e.g. "px:s:943694"
"""
from __future__ import annotations

PREFIX = "px"
SEP = ":"
CUSTOM_ID_MAX = 100

STAR = "s"
UNSTAR = "u"
CONFIRM = "sy"
CANCEL = "sn"
PREV = "p"
NEXT = "n"
NOW = "w"
RESET = "r"
SEARCH = "q"
SEL_DAY = "fd"
SEL_HOUR = "fh"
SEL_CATEGORY = "fc"

ALL_KINDS = (STAR, UNSTAR, CONFIRM, CANCEL, PREV, NEXT, NOW, RESET, SEARCH,
             SEL_DAY, SEL_HOUR, SEL_CATEGORY)


class IdError(Exception):
    """A custom_id could not be built or understood."""


def encode(kind: str, arg: str = "") -> str:
    if kind not in ALL_KINDS:
        raise IdError(f"unknown kind {kind!r}")
    if SEP in arg:
        raise IdError(f"argument {arg!r} contains the separator {SEP!r}")
    if not arg.isascii():
        # The 100 below is counted in Python code points. Discord has counted
        # some limits in UTF-16 units, where one astral-plane character costs
        # two - so a non-ASCII argument could pass this check and still leave
        # the component silently inert, the exact failure this module exists to
        # prevent. Every argument this project encodes is a numeric event id
        # (all 713 in the live PAX West payload are six ASCII digits), so
        # refusing non-ASCII costs nothing and turns a silent remote failure
        # into a loud local one.
        raise IdError(f"argument {arg!r} is not ASCII")
    built = f"{PREFIX}{SEP}{kind}{SEP}{arg}"
    if len(built) > CUSTOM_ID_MAX:
        raise IdError(
            f"custom_id is too long: {len(built)} > {CUSTOM_ID_MAX} for {built!r}"
        )
    return built


def decode(custom_id: str) -> tuple[str, str]:
    """Return (kind, arg). Raises IdError rather than returning a sentinel.

    Split with maxsplit=2 so an argument is taken whole; anything else would
    silently truncate on an unexpected separator.
    """
    parts = custom_id.split(SEP, 2)
    if len(parts) != 3 or parts[0] != PREFIX:
        raise IdError(f"not a paxbot custom_id: {custom_id!r}")
    _, kind, arg = parts
    if kind not in ALL_KINDS:
        raise IdError(f"unknown kind {kind!r} in {custom_id!r}")
    return kind, arg
