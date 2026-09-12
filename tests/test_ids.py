import pytest

from paxbot.bot import ids


def test_round_trips_with_an_argument():
    assert ids.decode(ids.encode(ids.STAR, "943694")) == (ids.STAR, "943694")


def test_round_trips_without_an_argument():
    assert ids.decode(ids.encode(ids.NEXT)) == (ids.NEXT, "")


def test_is_namespaced_so_other_apps_do_not_collide():
    assert ids.encode(ids.STAR, "1").startswith("px:")


def test_every_kind_fits_discords_limit_with_a_long_argument():
    """gt_ids are numeric and short, but the ceiling must hold regardless."""
    for kind in ids.ALL_KINDS:
        built = ids.encode(kind, "9" * 32)
        assert len(built) <= ids.CUSTOM_ID_MAX, kind


def test_rejects_an_argument_that_would_overflow():
    with pytest.raises(ids.IdError, match="too long"):
        ids.encode(ids.STAR, "9" * 200)


def test_rejects_a_separator_inside_the_argument():
    """A ':' in the argument would silently shift the decode boundary."""
    with pytest.raises(ids.IdError, match="separator"):
        ids.encode(ids.STAR, "12:34")


def test_rejects_an_unknown_kind_on_decode():
    with pytest.raises(ids.IdError, match="unknown kind"):
        ids.decode("px:nope:1")


def test_rejects_a_foreign_custom_id():
    with pytest.raises(ids.IdError, match="not a paxbot"):
        ids.decode("someoneelse:star:1")


def test_kinds_are_unique():
    assert len(set(ids.ALL_KINDS)) == len(ids.ALL_KINDS)


def test_accepts_an_id_of_exactly_the_limit():
    """100 characters is legal, 101 is not.

    Pinning the exact boundary catches two mutations that every other test in
    this file misses: an off-by-one in the check (`>` becoming `>=`), and a
    well-meaning raise of CUSTOM_ID_MAX itself. Discord reports neither - it
    just silently ignores the component.
    """
    longest = max(ids.ALL_KINDS, key=len)
    overhead = len(f"{ids.PREFIX}{ids.SEP}{longest}{ids.SEP}")
    built = ids.encode(longest, "9" * (ids.CUSTOM_ID_MAX - overhead))
    assert len(built) == ids.CUSTOM_ID_MAX == 100


def test_rejects_an_id_one_character_over_the_limit():
    longest = max(ids.ALL_KINDS, key=len)
    overhead = len(f"{ids.PREFIX}{ids.SEP}{longest}{ids.SEP}")
    with pytest.raises(ids.IdError, match="too long"):
        ids.encode(longest, "9" * (ids.CUSTOM_ID_MAX - overhead + 1))
