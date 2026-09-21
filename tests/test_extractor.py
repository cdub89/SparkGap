"""Callsign extraction from ITILA decoded text."""

import pytest

import sparkgap

DIGIT_FIRST = ["9A1AA", "4X4DK", "3A2MW", "4U1ITU", "5B4AIF", "2E0ABC"]


@pytest.mark.parametrize("tok", ["W1AW", "K9MA", "M7Z", "HB9AMO"])
def test_base_call_accepts(tok: str) -> None:
    assert sparkgap._is_base_call(tok)


@pytest.mark.parametrize("tok", ["HB9AMOHBM", "5NN", "5NN5TU", "TU", "599"])
def test_base_call_rejects(tok: str) -> None:
    assert not sparkgap._is_base_call(tok)


@pytest.mark.parametrize("tok", DIGIT_FIRST)
def test_base_call_digit_first_prefix(tok: str) -> None:
    assert sparkgap._is_base_call(tok)


@pytest.mark.parametrize(
    ("text", "call"),
    [
        ("CQ CQ DE W1AW W1AW K", "W1AW"),
        ("CQ TEST K9MA/P", "K9MA/P"),
        ("CQ PJ2 AG3I", "PJ2/AG3I"),
        ("CQ CQ E E A2JD K2JD K2JD K", "K2JD"),
        ("REST W1AW", "W1AW"),
        ("FB JIM N3BB DE N5RZ", None),
        ("CQ CQ 9A1AA 9A1AA K", "9A1AA"),
        ("CQ 4X4DK 4X4DK", "4X4DK"),
    ],
)
def test_extract_cq_call(text: str, call: str | None) -> None:
    assert sparkgap._itila_extract_cq_call(text) == call


def test_extract_all_calls_needs_repeats() -> None:
    assert sparkgap._itila_extract_all_calls("W1AW DE K2JD W1AW TU") == ["W1AW"]
