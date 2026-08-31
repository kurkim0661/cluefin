from __future__ import annotations

import pytest

from cluefin_agent.repository import display_number, display_percent


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (78601.4032035769, "USD", "78,601 USD"),
        (2676645303734.927, "USD", "2.68조 USD"),
        (8368057784.967, "USD", "8.37B USD"),
        (309048247.51, "USD", "309.05M USD"),
        (5791.7712, "USD bn", "5,792 USD bn"),
        (2.3212, "%", "2.32 %"),
        (0.0069173333, "%", "0.006917 %"),
        (673531, "count", "673,531 count"),
        (None, "USD", None),
    ],
)
def test_display_number_drops_noise_digits(value, unit, expected) -> None:
    assert display_number(value, unit) == expected


def test_display_number_marks_direction_for_changes() -> None:
    assert display_number(10.5, "%p", signed=True) == "+10.5 %p"
    assert display_number(-10.5, "%p", signed=True) == "-10.5 %p"
    assert display_number(1000, "count", signed=True) == "+1,000 count"


def test_display_percent_is_signed_and_short() -> None:
    assert display_percent(-0.0123456) == "-1.23%"
    assert display_percent(None) is None
