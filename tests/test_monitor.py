"""Click to decode (exp/click-decode): the scanner's monitor bin and the streaming decode."""

import ctypes as ct
from pathlib import Path

import numpy as np
import pytest
from test_scanner_fft_front import FS, _keyed_iq, _scanner

from itila2_monitor import JOIN_HOLD_S, CallJoin, Monitor, MonitorHub, shown, split_prosigns

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
    settled = [w.text for w in mon.words if w.commit_utc > 10.0]     # after the fits settle
    words = " ".join(settled).split()
    assert len(words) >= 25                                           # a commit holds whole words
    assert sum(t == "RN" for t in words) >= 0.9 * len(words)
    assert all(w.kept for w in mon.words)
    delays = sorted(w.commit_utc - w.end_utc for w in mon.words)
    assert delays[len(delays) // 2] < 2.0
    mon.close()
    lib.itila_sc_free(h)


def test_the_hub_opens_moves_and_drops_the_monitor() -> None:
    """A pick waits for a scanner and moves the monitor; a freed scanner is never touched."""
    dec_path = ROOT / "libitila2.so"
    if not dec_path.exists():
        pytest.skip("libitila2.so not built")
    lib, h = _scanner(False)
    lib = _monitor_lib(lib)
    hub = MonitorHub(ct.CDLL(str(dec_path)))
    band = [(lib, h, 1.5)]
    hub.request(TONE_HZ / 1000.0)
    assert hub.step([], 0.0) is None and hub.mon is None          # no scanner yet: the pick waits
    i, q = _keyed_iq(seconds=12.0)
    block = FS // 10
    steps = []
    for at in range(0, len(i), block):
        _feed(lib, h, i, q, at, min(block, len(i) - at))
        steps.append(hub.step(band, (at + block) / FS))
    assert hub.mon is not None and hub.mon.khz == TONE_HZ / 1000.0
    assert any(st and st.words for st in steps)
    hub.request(13_000.0)                                         # on no band: closed, nothing open
    hub.step(band, 12.5)
    assert hub.mon is None
    hub.request(TONE_HZ / 1000.0)
    hub.step(band, 13.0)
    assert hub.mon is not None
    lib.itila_sc_free(h)                                          # a band change frees the scanner
    assert hub.step([], 13.5) is None and hub.mon is None


@pytest.mark.parametrize("fir1", [False, True])
def test_a_monitor_on_a_signal_with_a_bin_starts_from_its_envelope(fir1: bool) -> None:
    """Opened 8 s into a keyed signal its bin holds: seconds of history, not the 1 s pre-roll."""
    lib, h = _scanner(fir1)
    lib = _monitor_lib(lib)
    i, q = _keyed_iq(seconds=9.0)
    _feed(lib, h, i, q, 0, 8 * FS)
    fb = np.empty(64)
    assert lib.itila_sc_list_bins(h, fb.ctypes.data_as(P), 64) >= 1       # the keyed tone's bin
    assert lib.itila_sc_monitor_open(h, TONE_HZ) == 1
    _feed(lib, h, i, q, 8 * FS, FS)
    env, end = np.empty(8000), ct.c_longlong(0)
    n = lib.itila_sc_monitor_read(h, None, env.ctypes.data_as(P), 8000, ct.byref(end), None)
    assert n > 5 * 200 and end.value == n
    lib.itila_sc_free(h)


@pytest.mark.parametrize("text, ev, ok", [
    ("JAKE", 6602.0, True), ("E", 1815.0, False), ("T", 2426.0, True), ("K", 588.0, True),
    ("EI", 250.0, False), ("?", None, True),
])
def test_a_stretch_shows_with_evidence_of_its_own(text: str, ev: float | None, ok: bool) -> None:
    """Noise between overs: low evidence, and a lone E or T needs more (N9FGC QSO, 6 dB, 11-26)."""
    assert shown(text, ev) is ok


KNOWN = ["KM7AZ", "N9FGC", "WB0RLJ", "W1DV", "5NNHR"]


@pytest.mark.parametrize("text, joined", [
    ("KM7A Z TU GM", "KM7AZ TU GM"),            # LakeDog 11-26: a gap in a call read as a word gap
    ("DEN 9 F GC NT", "DE N9FGC NT"),           # N9FGC at 6 dB, DE run into the call
    ("73 WB0RLJ EE", "73 WB0RLJ EE"),           # a known call stays as it is
    ("TU 5NN 5NN K", "TU 5NN 5NN K"),           # nothing joins that is not a known call
    ("R 5NN HR STEVE", "R 5NN HR STEVE"),       # 5NNHR is on the RBN list, but 5NN is no call piece
])
def test_split_calls_join_into_known_calls(text: str, joined: str) -> None:
    assert " ".join(CallJoin(KNOWN).join(text.split())) == joined


def test_a_call_split_across_commits_waits_for_its_rest() -> None:
    """N9FGC came as three commits (DEN 9 | F | GC NT); a tail that never completes still shows."""
    hub = MonitorHub(ct.CDLL(None))             # no decoder needed: only the text path
    hub.set_known(KNOWN)
    shown_at = [hub._shown(["DEN 9"], 0.0), hub._shown(["F"], 1.0), hub._shown(["GC NT"], 2.0)]
    assert shown_at == ["", "", "DE N9FGC NT"]
    assert hub._shown(["QSL KM7A"], 3.0) == "QSL"
    assert hub._shown([], 3.0 + JOIN_HOLD_S / 2) == ""
    assert hub._shown([], 3.0 + JOIN_HOLD_S) == "KM7A"


def test_common_words_are_not_held_as_call_pieces() -> None:
    """TU, GM, ES, K start known calls (TU5, GM3, ES1, K1): only a piece with its digit waits."""
    hub = MonitorHub(ct.CDLL(None))
    hub.set_known(KNOWN + ["TU5AB", "GM3ABC", "ES1AB", "K1ABC"])
    assert [hub._shown([t], 0.0) for t in ("TU", "GM ES", "K")] == ["TU", "GM ES", "K"]


@pytest.mark.parametrize("text, split", [
    ("UR 5NN5NNBK", "UR 5NN5NN BK"),            # LakeDog 11-26
    ("BKTUJIM", "BK TUJIM"),
    ("TNX 73KN", "TNX 73 KN"),
    ("KNOW SKILL YEAR DESK CLEAR", "KNOW SKILL YEAR DESK CLEAR"),   # words, not prosigns
    ("73AR TNXAR", "73 AR TNX AR"),
    ("K1BK DE W1AR", "K1BK DE W1AR"),           # calls keep their letters
    ("BK TU", "BK TU"),
])
def test_prosigns_run_into_a_word_are_split_off(text: str, split: str) -> None:
    assert " ".join(split_prosigns(text.split())) == split
