"""Tests for créneau / rotation helpers."""

from datetime import date

from custom_components.irrigationprogram.rotation import (
    ROTATION_EPOCH,
    creneau_to_start_date,
    days_until_creneau,
    start_date_to_creneau,
)


def test_creneau_to_start_date_slots():
    assert creneau_to_start_date(1) == "2026-01-01"
    assert creneau_to_start_date(2) == "2026-01-02"
    assert creneau_to_start_date(3) == "2026-01-03"
    assert creneau_to_start_date("7") == "2026-01-07"


def test_start_date_to_creneau_adjacent():
    assert start_date_to_creneau("2026-01-01") == "1"
    assert start_date_to_creneau("2026-01-02") == "2"
    # Existing half/half dates map to distinct slots within 1..7
    a = start_date_to_creneau("2026-07-23")
    b = start_date_to_creneau("2026-07-24")
    assert a != b
    assert a in {str(i) for i in range(1, 8)}
    assert b in {str(i) for i in range(1, 8)}


def test_days_until_creneau_rotation_freq3():
    """With epoch Jan 1 2026 (Thursday): slots 1/2/3 never collide."""
    # 2026-07-23 is 203 days after epoch → 203 % 3 == 2 → créneau 3 runs
    d = date(2026, 7, 23)
    assert (d - ROTATION_EPOCH).days % 3 == 2
    assert days_until_creneau(d, 3, 3) == 0
    assert days_until_creneau(d, 3, 1) == 1
    assert days_until_creneau(d, 3, 2) == 2

    d2 = date(2026, 7, 24)
    assert days_until_creneau(d2, 3, 1) == 0
    assert days_until_creneau(d2, 3, 2) == 1
    assert days_until_creneau(d2, 3, 3) == 2


def test_freq2_two_slots_alternate():
    hits = {(days_until_creneau(date(2026, 7, d), 2, 1) == 0) for d in range(23, 29)}
    # Across 6 days, créneau 1 should run on some days
    assert True in hits
    for d in range(23, 29):
        day = date(2026, 7, d)
        a = days_until_creneau(day, 2, 1) == 0
        b = days_until_creneau(day, 2, 2) == 0
        assert a != b  # never both, never neither on a 2-slot full rotation
