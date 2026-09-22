"""Pure timing fits in itila2_core.c: fit_unit and fit_letter_word_boundary.

Pinned to real run lists captured with ITILA2_DUMP_RUNS on 2026-09-22, so a
later change to the fits cannot silently undo them. Exercised through the
itila2_test_fit_unit / itila2_test_fit_letter_word ctypes hooks (200 Hz
envelope samples, same units as the dump)."""

import ctypes
from pathlib import Path

import pytest

_LIB_PATH = Path(__file__).resolve().parent.parent / "libitila2.so"
if not _LIB_PATH.exists():
    pytest.skip("libitila2.so not built", allow_module_level=True)

_lib = ctypes.CDLL(str(_LIB_PATH))

_lib.itila2_test_fit_unit.argtypes = [
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_double),
]
_lib.itila2_test_fit_unit.restype = ctypes.c_double

_lib.itila2_test_fit_letter_word.argtypes = [
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_int),
]
_lib.itila2_test_fit_letter_word.restype = ctypes.c_double

# W6TED, 24 WPM, bin 14059.0, 20m run 2 (2026-09-22): the complete run list of the
# window that decoded 'W6TED ? DE W6 TE D', captured with ITILA2_DUMP_RUNS. Dits are
# 11 to 12 samples, dahs 33 to 34; the EM unit that day was 4.00.
W6TED_RUNS = (
    "-14 +12 -38 +11 -11 +11 -11 +34 -8 +12 -27 +33 -11 +11 -10 +12 -52 "
    "+33 -11 +29 -35 +10 -11 +32 -41 +28 -36 +32 -88 +8 -11 +10 -12 +6 -6 "
    "+21 -12 +9 -13 +28 -14 +11 -50 +8 -14 +9 -14 +8 -36 +5 -60 +9 -12 "
    "+11 -35 +9 -78 +16 -24 +2 -67 +10 -12 +5 -71 +7 -5 +8 -4 +5 -15 +9 "
    "-62 +13 -27 +6 -20 +4 -13 +10 -16 +9 -10 +8 -15 +5 -19 +4 -11 +7 "
    "-1726 +18 -26 +11 -11 +11 -13 +30 -29 +9 -31 +14 -4298 +4 -243 +2 "
    "-281 +3 -398 +3 -455 +3 -73 +3 -1448 +2 -17 +5 -1091"
)

# KE4D, 17 WPM, bin 14061.5, 20m run 2 (2026-09-22): the complete run list of the
# window that decoded 'KE4 D' (its known defect); captured with ITILA2_DUMP_RUNS.
# Dits 12 to 14 samples, dahs 38 to 42; the EM unit that day was 12.24.
KE4D_RUNS = (
    "-4 +12 -16 +11 -16 +12 -2577 +38 -14 +13 -15 +42 -14 +14 -14 +42 -14 "
    "+42 -14 +14 -14 +42 -59 +42 -14 +14 -15 +41 -14 +14 -14 +42 -14 +42 "
    "-14 +14 -14 +42 -72 +14 -14 +42 -14 +42 -14 +14 -32 +42 -14 +42 -14 "
    "+42 -37 +42 -23 +15 -14 +41 -66 +14 -14 +42 -14 +42 -42 +14 -14 +14 "
    "-14 +14 -14 +43 -13 +42 -45 +14 -14 +14 -14 +14 -41 +42 -13 +15 -13 "
    "+43 -32 +42 -14 +42 -14 +14 -1027 +38 -14 +14 -14 +42 -27 +14 -47 "
    "+14 -14 +14 -14 +14 -14 +13 -16 +41 -60 +42 -14 +14 -15 +13 -104 +41 "
    "-42 +14 -14 +13 -15 +42 -45 +40 -35 +12 -15 +13 -16 +41 -53 +7 -20 "
    "+12 -18 +14 -4 +8 -4 +4 -30 +1 -50 +8 -989 +2 -22 +7 -2764 +37 -32 "
    "+14 -14 +14 -14 +42 -37 +41 -20 +13 -15 +13 -15 +41 -33 +14 -15 +12 "
    "-16 +41 -15 +13 -32 +14 -14 +41 -15 +14 -14 +13 -76 +42 -14 +42 -14 "
    "+12 -16 +13 -20 +8 -65 +13 -14 +6 -2 +3 -23 +8 -20"
)


def runs(spec: str) -> tuple[list[int], list[int]]:
    """Parse a dump-style run string into (is_mark, dur) lists. Tokens
    starting with + are marks, - are spaces; the sign is stripped."""
    is_mark: list[int] = []
    dur: list[int] = []
    for tok in spec.split():
        is_mark.append(1 if tok.startswith("+") else 0)
        dur.append(abs(int(tok)))
    return is_mark, dur


def call_fit_unit(
    spec: str, unit_in: float
) -> tuple[float, int, float]:
    """Call itila2_test_fit_unit on a parsed run spec."""
    is_mark, dur = runs(spec)
    n = len(is_mark)
    is_mark_arr = (ctypes.c_int * n)(*is_mark)
    dur_arr = (ctypes.c_int * n)(*dur)
    fitted = ctypes.c_int()
    dah_out = ctypes.c_double()
    unit = _lib.itila2_test_fit_unit(
        is_mark_arr, dur_arr, n, unit_in, ctypes.byref(fitted), ctypes.byref(dah_out)
    )
    return unit, fitted.value, dah_out.value


def call_fit_letter_word(spec: str, unit: float) -> tuple[float, int]:
    """Call itila2_test_fit_letter_word on a parsed run spec."""
    is_mark, dur = runs(spec)
    n = len(is_mark)
    is_mark_arr = (ctypes.c_int * n)(*is_mark)
    dur_arr = (ctypes.c_int * n)(*dur)
    fitted = ctypes.c_int()
    boundary = _lib.itila2_test_fit_letter_word(
        is_mark_arr, dur_arr, n, unit, ctypes.byref(fitted)
    )
    return boundary, fitted.value


def test_fit_unit_w6ted() -> None:
    """Pinned behaviour: with the EM unit of 4.00 the mark-run fit returns 8.68
    for 11 to 12 sample dits (low, pulled by short noise marks) and a dah near
    27. A fix that lands nearer 11.5 / 33 should move these bounds up."""
    unit, fitted, dah_out = call_fit_unit(W6TED_RUNS, 4.0)
    assert fitted == 1
    assert 8.0 <= unit <= 12.5
    assert 24.0 <= dah_out <= 36.0


def test_fit_unit_ke4d() -> None:
    """With the EM unit of 12.24 the mark-run fit returns 12.65 for 12 to 14
    sample dits and a dah near 41, both within the operator's real timing."""
    unit, fitted, dah_out = call_fit_unit(KE4D_RUNS, 12.24)
    assert fitted == 1
    assert 11.7 <= unit <= 14.3
    assert 35.0 <= dah_out <= 47.0


def test_fit_unit_too_few_marks() -> None:
    """Fewer than UNIT_FIT_MIN_MARKS interior marks (5 here): the passed
    unit is returned unchanged and nothing is fitted."""
    five_marks = "-100 +10 -100 +10 -100 +10 -100 +10 -100 +10 -100"
    unit, fitted, dah_out = call_fit_unit(five_marks, 7.82)
    assert unit == 7.82
    assert fitted == 0
    assert dah_out == 0.0


def test_letter_word_w6ted() -> None:
    """Pinned defect: at the fitted unit 8.68 the boundary lands near 44
    samples, under this operator's 46 to 52 sample letter gaps, which is why
    the window read 'W6 TE D'. A fix must place it between 53 and 64 samples
    (letters stay letters, the 65 sample word gaps stay words)."""
    boundary, fitted = call_fit_letter_word(W6TED_RUNS, 8.68)
    assert fitted == 1
    assert 40.0 <= boundary <= 48.0


def test_letter_word_ke4d_known_split() -> None:
    """Pinned defect: at the fitted unit 12.65 the boundary lands near 3.9
    units (49 samples), under KE4D's 49 sample letter gap, so 'KE4 D' splits.
    A fix must move it above 52 samples; this test then needs updating."""
    boundary, fitted = call_fit_letter_word(KE4D_RUNS, 12.65)
    assert fitted == 1
    assert 44.0 <= boundary <= 56.0


def test_letter_word_falls_back_without_word_gaps() -> None:
    """Only element gaps (about 1 unit) and letter gaps (about 3 units),
    no word-length gaps: the fit does not find three classes and the fixed
    5-unit rule is used."""
    only_element_and_letter = (
        "-10 +10 -11 +10 -30 +10 -29 +10 -10 +10 -31 +10 "
        "-11 +10 -30 +10 -10 +10 -10 +10 -29 +10 -31 +10"
    )
    boundary, fitted = call_fit_letter_word(only_element_and_letter, 10.0)
    assert fitted == 0
    assert boundary == 5.0 * 10.0


def test_letter_word_ignores_silence() -> None:
    """Gaps far longer than GAP_FIT_MAX_UNITS (idle time, not word gaps)
    are excluded from the fit, so adding two of them does not move the
    boundary."""
    boundary_before, _ = call_fit_letter_word(W6TED_RUNS, 10.0)
    with_silence = W6TED_RUNS + " -3000 +10 -3000"
    boundary_after, _ = call_fit_letter_word(with_silence, 10.0)
    assert abs(boundary_after - boundary_before) <= 1.0
