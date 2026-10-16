"""Band names in bands[]: the IQ span starts at the band's lower CW edge at any sample rate."""

import pytest

from sparkgap import BANDS, CW_BAND_PLAN_R2, SparkGap, band_center_hz

EDGE_KHZ: dict[str, int] = {str(k): v for k, v in BANDS.items()}


@pytest.mark.parametrize("rate", [48_000, 96_000, 192_000])
@pytest.mark.parametrize("name", list(BANDS))
def test_span_starts_at_band_edge(name: str, rate: int) -> None:
    low_hz = band_center_hz(name, rate) - rate // 2
    assert low_hz == EDGE_KHZ[name] * 1000


def test_names_follow_the_band_plan() -> None:
    assert list(BANDS.values()) == [row[0] for row in CW_BAND_PLAN_R2]
    assert band_center_hz("40m", 96_000) == 7_048_000
    assert band_center_hz("20m", 192_000) == 14_096_000


def test_hz_and_dict_entries_unchanged() -> None:
    assert SparkGap._resolve_band("40m", 96_000) == ("40m", 7_048_000)
    assert SparkGap._resolve_band(7_090_000, 96_000) == ("7090000", 7_090_000)
    assert SparkGap._resolve_band({"center_khz": 7090}, 192_000) == ("7090", 7_090_000)
    assert SparkGap._resolve_band({"center_hz": 14_050_000, "name": "20m"}, 48_000) == (
        "20m", 14_050_000)
