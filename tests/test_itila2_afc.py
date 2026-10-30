"""itila2_afc: each bin re-centres its envelope paths on the keyed tone."""

import ctypes
import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
SC = str(ROOT / "libitila2_scanner.so")
pytestmark = pytest.mark.skipif(not os.path.exists(SC), reason="libitila2_scanner not built")
D = ctypes.POINTER(ctypes.c_double)
WIN = 6000
CENTER, TONE = 7_030_000.0, 5000.0


def _lib() -> ctypes.CDLL:
    sc = ctypes.CDLL(SC)
    sc.itila_sc_create.restype = ctypes.c_void_p
    sc.itila_sc_create.argtypes = [
        ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_int,
        ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_void_p, ctypes.c_int,
        ctypes.c_void_p,
    ]  # fmt: skip
    sc.itila_sc_feed_iq.argtypes = [ctypes.c_void_p, D, D, ctypes.c_int]
    sc.itila_sc_set_afc.restype = ctypes.c_int
    sc.itila_sc_set_afc.argtypes = [ctypes.c_void_p, ctypes.c_int]
    sc.itila_sc_get_afc_hz.restype = ctypes.c_double
    sc.itila_sc_get_afc_hz.argtypes = [ctypes.c_void_p, ctypes.c_double]
    sc.itila_sc_drain_env.restype = ctypes.c_int
    sc.itila_sc_drain_env.argtypes = [ctypes.c_void_p, ctypes.c_double, D, D, ctypes.c_int]
    sc.itila_sc_list_bins.restype = ctypes.c_int
    sc.itila_sc_list_bins.argtypes = [ctypes.c_void_p, D, ctypes.c_int]
    sc.itila_sc_free.argtypes = [ctypes.c_void_p]
    return sc


def _create(sc: ctypes.CDLL, fs: int) -> int:
    lo, hi = 7_012_000.0, 7_048_000.0
    return sc.itila_sc_create(fs, CENTER, 50, 12.0, WIN, 4096, 50.0, lo, hi, None, 0, None)


def _run(fs: int, segments: Sequence[tuple[float, float | None]], afc: int):
    """Feed keyed CW (25 WPM dits) per segment: (seconds, tone offset in Hz, None = silence).
    Returns the narrow envelope per segment and the bin's AFC offset at the end of each."""
    sc = _lib()
    h = _create(sc, fs)
    rng = np.random.default_rng(2)
    block = fs // 10
    dit = int(0.048 * fs)
    f_bin, phase, t = None, 0.0, 0
    env: list[list[float]] = []
    afc_at: list[float] = []
    try:
        sc.itila_sc_set_afc(h, afc)
        for secs, off in segments:
            seg: list[float] = []
            for _ in range(int(secs * 10)):
                n = np.arange(t, t + block)
                z = 1e-4 * (rng.standard_normal(block) + 1j * rng.standard_normal(block))
                if off is not None:
                    ph = phase + np.cumsum(np.full(block, 2 * np.pi * (TONE + off) / fs))
                    z = z + 0.01 * ((n // dit) % 2 == 0) * np.exp(1j * ph)
                    phase = float(ph[-1])
                t += block
                i, q = np.ascontiguousarray(z.real), np.ascontiguousarray(z.imag)
                sc.itila_sc_feed_iq(h, i.ctypes.data_as(D), q.ctypes.data_as(D), block)
                bins = (ctypes.c_double * 64)()
                for j in range(sc.itila_sc_list_bins(h, bins, 64)):
                    if abs(bins[j] - (CENTER + TONE)) < 30:
                        f_bin = bins[j]
                if f_bin is not None:
                    e1, e2 = np.empty(WIN * 2), np.empty(WIN * 2)
                    p1, p2 = e1.ctypes.data_as(D), e2.ctypes.data_as(D)
                    m = sc.itila_sc_drain_env(h, f_bin, p1, p2, WIN * 2)
                    seg.extend(e1[:m].tolist())
            env.append(seg)
            afc_at.append(sc.itila_sc_get_afc_hz(h, f_bin) if f_bin is not None else float("nan"))
    finally:
        sc.itila_sc_free(h)
    assert f_bin is not None
    return env, afc_at


def _level(seg: list[float], last_s: float) -> float:
    return float(np.percentile(np.array(seg[-int(last_s * 200) :]), 95))


def test_afc_is_off_until_set_and_toggles() -> None:
    sc = _lib()
    h = _create(sc, 48_000)
    try:
        assert sc.itila_sc_set_afc(h, 1) == 1
        assert sc.itila_sc_set_afc(h, 0) == 0
    finally:
        sc.itila_sc_free(h)


def test_a_tone_that_moves_35_hz_stays_in_the_narrow_path_with_afc() -> None:
    plan = [(20.0, 0.0), (20.0, 35.0)]
    (on0, on1), _ = _run(48_000, plan, 1)
    (off0, off1), _ = _run(48_000, plan, 0)
    assert _level(off1, 10) < 0.5 * _level(off0, 8)  # off: the moved tone leaves narrow
    assert _level(on1, 10) > 0.9 * _level(on0, 8)  # on: the bin follows it


@pytest.mark.parametrize("fs", [48_000, 192_000])
@pytest.mark.parametrize("shift", [35.0, -35.0])
def test_afc_settles_on_the_tone(fs: int, shift: float) -> None:
    _, afc = _run(fs, [(15.0, 0.0), (10.0, shift)], 1)
    assert abs(afc[0]) < 6.0  # the bin spawns within a few Hz of the tone; afc takes up the rest
    assert abs(afc[1] - afc[0] - shift) < 2.0


def test_silence_does_not_move_afc() -> None:
    _, afc = _run(48_000, [(15.0, 0.0), (5.0, 35.0), (10.0, None)], 1)
    assert abs(afc[1] - afc[0] - 35.0) < 2.0
    assert abs(afc[2] - afc[1]) < 1.0


def test_a_neighbour_100_hz_off_does_not_pull_the_bin() -> None:
    # the bin's station stops and a station 100 Hz up keys: the bin stays on its own tone
    _, afc = _run(48_000, [(15.0, 0.0), (15.0, 100.0)], 1)
    assert abs(afc[1] - afc[0]) < 5.0
