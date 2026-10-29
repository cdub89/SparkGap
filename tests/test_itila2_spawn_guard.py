"""itila2_spawn_guard: no bin for a strong station's skirts; a station beside it still gets one."""

import ctypes

import numpy as np
import pytest

from tests.test_itila2_afc import CENTER, SC, TONE, D, _create, _lib, pytestmark  # noqa: F401

STRONG, SPUR = 1e-2, 3e-4  # 30 dB apart
SIDE = 100.0  # Hz beside the strong tone


def _bins_beside(guard: int, beside_keyed_with_strong: bool) -> list[float]:
    """Feed 20 s: a strong tone keyed 0.5 s on, 0.5 s off, and a weak tone SIDE Hz up, keyed either
    with it (a skirt) or in its gaps (another station).
    Returns the bins spawned beside the strong one."""
    sc = _lib()
    sc.itila_sc_set_spawn_guard.restype = ctypes.c_int
    sc.itila_sc_set_spawn_guard.argtypes = [ctypes.c_void_p, ctypes.c_int]
    fs = 48_000
    h = _create(sc, fs)
    rng = np.random.default_rng(3)
    block, dit = fs // 10, fs // 2  # slow keying: no keying sidebands near SIDE
    found: set[float] = set()
    try:
        assert sc.itila_sc_set_spawn_guard(h, guard) == guard
        for b in range(200):
            n = np.arange(b * block, (b + 1) * block)
            on = ((n // dit) % 2 == 0).astype(float)
            beside = on if beside_keyed_with_strong else 1.0 - on
            w = 2 * np.pi * n / fs
            z = 1e-4 * (rng.standard_normal(block) + 1j * rng.standard_normal(block))
            z = (
                z
                + STRONG * on * np.exp(1j * w * TONE)
                + SPUR * beside * np.exp(1j * w * (TONE + SIDE))
            )
            i, q = np.ascontiguousarray(z.real), np.ascontiguousarray(z.imag)
            sc.itila_sc_feed_iq(h, i.ctypes.data_as(D), q.ctypes.data_as(D), block)
            bins = (ctypes.c_double * 64)()
            for j in range(sc.itila_sc_list_bins(h, bins, 64)):
                if 60 < bins[j] - (CENTER + TONE) < 160:
                    found.add(round(bins[j]))
    finally:
        sc.itila_sc_free(h)
    return sorted(found)


@pytest.mark.parametrize("guard", [0, 1])
def test_a_skirt_spawns_a_bin_only_without_the_guard(guard: int) -> None:
    assert bool(_bins_beside(guard, beside_keyed_with_strong=True)) == (guard == 0)


def test_a_station_keying_in_the_gaps_still_spawns_with_the_guard() -> None:
    assert _bins_beside(1, beside_keyed_with_strong=False)
