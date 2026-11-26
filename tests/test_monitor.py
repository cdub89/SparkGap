"""Click to decode (exp/click-decode): the scanner's monitor bin and the streaming decode."""

import ctypes as ct
from pathlib import Path

import numpy as np
import pytest
from test_scanner_fft_front import FS, _keyed_iq, _scanner

from itila2_monitor import Monitor

ROOT = Path(__file__).resolve().parent.parent
TONE_HZ = 14028063.0       # the keyed tone of _keyed_iq: "RN", 22 WPM
FEED = 960                 # ~5 ms feeds, as the live C path does
P = ct.POINTER(ct.c_double)


def _monitor_lib(lib: ct.CDLL) -> ct.CDLL:
    if not hasattr(lib, "itila_sc_monitor_open"):
        pytest.skip("libitila2_scanner.so has no monitor")
    lib.itila_sc_monitor_open.restype = ct.c_int
    lib.itila_sc_monitor_open.argtypes = [ct.c_void_p, ct.c_double]
    lib.itila_sc_monitor_close.argtypes = [ct.c_void_p]
    lib.itila_sc_monitor_read.restype = ct.c_int
    lib.itila_sc_monitor_read.argtypes = [ct.c_void_p, P, P, ct.c_int, ct.POINTER(ct.c_longlong), P]
    return lib


def _feed(lib: ct.CDLL, h: int, i: np.ndarray, q: np.ndarray, at: int, n: int) -> None:
    for p in range(at, at + n, FEED):
        a, b = i[p:p + FEED], q[p:p + FEED]
        lib.itila_sc_feed_iq(h, a.ctypes.data_as(P), b.ctypes.data_as(P), len(a))


def _bins_and_envelopes(fir1: bool, monitor: bool) -> list[tuple[float, list[float]]]:
    lib, h = _scanner(fir1)
    lib = _monitor_lib(lib)
    i, q = _keyed_iq(seconds=8.0)
    if monitor:
        assert lib.itila_sc_monitor_open(h, TONE_HZ + 30.0) == 1
    _feed(lib, h, i, q, 0, len(i))
    fb = np.empty(64)
    out = []
    for f in fb[:lib.itila_sc_list_bins(h, fb.ctypes.data_as(P), 64)].tolist():
        e1, e2 = np.empty(15000), np.empty(15000)
        n = lib.itila_sc_drain_env(h, f, e1.ctypes.data_as(P), e2.ctypes.data_as(P), 15000)
        out.append((f, e2[:n].tolist()))
    lib.itila_sc_free(h)
    return out


@pytest.mark.parametrize("fir1", [False, True])
def test_a_monitor_changes_no_bin(fir1: bool) -> None:
    """Same bins and the same envelopes with a monitor open on the signal: spots can't move."""
    assert _bins_and_envelopes(fir1, True) == _bins_and_envelopes(fir1, False)


def test_monitor_read_open_close() -> None:
    lib, h = _scanner(False)
    lib = _monitor_lib(lib)
    env, end = np.empty(8000), ct.c_longlong(0)
    assert lib.itila_sc_monitor_read(h, None, env.ctypes.data_as(P), 8000, ct.byref(end), None) == 0
    assert lib.itila_sc_monitor_open(h, 13_000_000.0) == 0          # outside the band
    assert lib.itila_sc_monitor_open(h, TONE_HZ) == 1
    i, q = _keyed_iq(seconds=3.0)
    _feed(lib, h, i, q, 0, len(i))
    assert lib.itila_sc_monitor_read(h, None, env.ctypes.data_as(P), -1, ct.byref(end), None) == 0
    n = lib.itila_sc_monitor_read(h, None, env.ctypes.data_as(P), 8000, ct.byref(end), None)
    assert n > 400 and end.value == n                                 # 1 s pre-roll + 3 s at 200 Hz
    assert env[:n].max() > 10 * np.median(env[:n])                    # keyed: marks above the gaps
    lib.itila_sc_monitor_close(h)
    assert lib.itila_sc_monitor_read(h, None, env.ctypes.data_as(P), 8000, ct.byref(end), None) == 0
    lib.itila_sc_free(h)


def test_monitor_commits_each_word_once() -> None:
    """40 s of "RN" at 22 WPM: the words come out one commit each, in under 2 s."""
    dec_path = ROOT / "libitila2.so"
    if not dec_path.exists():
        pytest.skip("libitila2.so not built")
    lib, h = _scanner(False)
    lib = _monitor_lib(lib)
    dec = ct.CDLL(str(dec_path))
    if not hasattr(dec, "itila2_stream_step"):
        pytest.skip("libitila2.so has no itila2_stream_step")
    mon = Monitor(lib, h, dec, ev_thresh=1.5)
    assert mon.open(TONE_HZ / 1000.0)
    i, q = _keyed_iq(seconds=40.0)
    block = FS // 10
    for at in range(0, len(i), block):
        _feed(lib, h, i, q, at, min(block, len(i) - at))
        mon.feed((at + block) / FS)
    texts = [w.text for w in mon.words if w.commit_utc > 10.0]       # after the fits settle
    assert len(texts) >= 25
    assert sum(t == "RN" for t in texts) >= 0.9 * len(texts)
    delays = sorted(w.commit_utc - w.end_utc for w in mon.words)
    assert delays[len(delays) // 2] < 2.0
    mon.close()
    lib.itila_sc_free(h)
