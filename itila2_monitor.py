"""Click to decode (exp/click-decode): one monitor at an exact frequency, decoded in near real time.

The scanner's monitor bin (itila_sc_monitor_open) makes the envelope with the bins' own DSP
(AFC, tone lock); every STEP_S of audio this decodes the newest WINDOW_S of it with
itila2_stream_step, so the fits are a 30 s window's, and commits the words the window cut has
passed since the last step.  Display only: spots, bins and windows never see it.
"""

import collections
import ctypes as ct
import logging
import math
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

RATE_HZ = 200          # envelope samples per second
WINDOW_S = 30.0        # decoded each step, as a batch window
STEP_S = 0.5           # audio between steps
ARMS = (("wide", 200.0), ("narrow", 100.0))   # as the scanner's h200 and h100; wide sets the cut
TEXT_LEN = 1024
SEG_EV_MIN = 300.0     # a stretch's own evidence below this: noise between overs (net 10-07, log)
NO_FIT = 999.0         # itila_get_last_cost when there was nothing to time
HOLD_STEPS = 3         # steps in a row the window must pass the evidence test before text shows:
                       # at 6 dB noise passed some of the 60 tries per 30 s (LakeDog 11-26, N9FGC)
HOLD_BACK_S = 1.0      # a failed step moves the commit to this long before now: quiet stays quiet
LONE_EV_MIN = 2000.0   # a lone E or T needs this much evidence (noise E/T: 590-1815, N9FGC QSO);
                       # per second of key-down, noise letters scored as high as words (11-26)
HEARD_BONUS = 2.0      # token prior on top of TOKEN_PRIOR for a call or prosign heard in this QSO
QSO_PROSIGNS = ("BK", "BT", "KN", "SK", "AR")

P = ct.POINTER(ct.c_double)
log = logging.getLogger("sparkgap")


@dataclass
class Word:
    end_utc: float      # audio time of the text's last key-down
    commit_utc: float   # audio time of the step that committed it
    wpm: float | None
    text: str
    ev: float | None = None   # the stretch's own CW evidence (log Bayes factor)
    kept: bool = True         # ev passed SEG_EV_MIN: shown; else logged only, for tuning
    arm: str = "wide"         # the filter whose reading was cheaper (the windows' Best)
    other: str = ""           # the other arm's reading, for the log


def _bind(sc_lib: ct.CDLL, dec_lib: ct.CDLL) -> bool:
    if not hasattr(sc_lib, 'itila_sc_monitor_open') or not hasattr(dec_lib, 'itila2_set_monitor'):
        return False
    sc_lib.itila_sc_monitor_open.restype = ct.c_int
    sc_lib.itila_sc_monitor_open.argtypes = [ct.c_void_p, ct.c_double]
    sc_lib.itila_sc_monitor_close.restype = None
    sc_lib.itila_sc_monitor_close.argtypes = [ct.c_void_p]
    sc_lib.itila_sc_monitor_read.restype = ct.c_int
    sc_lib.itila_sc_monitor_read.argtypes = [ct.c_void_p, P, P, ct.c_int,
                                             ct.POINTER(ct.c_longlong), P]
    dec_lib.itila_create.restype = ct.c_void_p
    dec_lib.itila_create.argtypes = [ct.c_int, ct.c_double]
    dec_lib.itila_free.restype = None
    dec_lib.itila_free.argtypes = [ct.c_void_p]
    dec_lib.itila_get_wpm.restype = ct.c_double
    dec_lib.itila_get_wpm.argtypes = [ct.c_void_p]
    dec_lib.itila_get_last_cost.restype = ct.c_double
    dec_lib.itila_get_last_cost.argtypes = [ct.c_void_p]
    dec_lib.itila2_set_monitor.restype = ct.c_int
    dec_lib.itila2_set_monitor.argtypes = [ct.c_void_p, ct.c_int, ct.c_char_p, ct.c_double]
    dec_lib.itila2_stream_step.restype = ct.c_int
    dec_lib.itila2_stream_step.argtypes = [ct.c_void_p, P, ct.c_int, ct.c_int, ct.c_double,
                                           ct.c_char_p, ct.c_char_p, ct.c_int, ct.POINTER(ct.c_int),
                                           P, ct.c_int]
    return True


@dataclass
class _Reading:
    cut: int
    text: str
    live: str
    last: int
    ev: float | None
    cost: float
    wpm: float | None


def narrow_wins(wide: str, wide_cost: float, narrow: str, narrow_cost: float,
                known: frozenset[str]) -> bool:
    """Whether the narrow arm's reading of a stretch is shown rather than the wide one's."""
    if not narrow or not wide:
        return False          # narrow alone read noise (net 10-07)
    if wide.replace(" ", "") == narrow.replace(" ", ""):
        return narrow.count(" ") < wide.count(" ")   # same letters: fewer spaces (G OT, a bug)
    calls_w = sum(w in known for w in wide.split())
    calls_n = sum(w in known for w in narrow.split())
    if calls_w != calls_n:
        return calls_n > calls_w                     # a known call decides (KF9VV vs KF9V4)
    return narrow_cost < min(wide_cost, NO_FIT)      # else timing cost, as the windows


class _Arm:
    """One filter's decoder: its own handle and envelope (the fits warm-start step to step)."""

    def __init__(self, dec_lib: ct.CDLL, lpf_hz: float) -> None:
        self._lib = dec_lib
        self.h: int | None = dec_lib.itila_create(RATE_HZ, lpf_hz)
        self.env = np.zeros(int(WINDOW_S * RATE_HZ))
        self._stable = ct.create_string_buffer(TEXT_LEN)
        self._live = ct.create_string_buffer(TEXT_LEN)
        self._last = ct.c_int(-1)
        self._ev = ct.c_double(0.0)
        self.words(())

    def words(self, heard: Iterable[str]) -> None:
        """Time each over on its own; the calls heard in this QSO and the prosigns earn a prior."""
        if self.h:
            text = "\n".join(sorted(set(heard) | set(QSO_PROSIGNS)))
            self._lib.itila2_set_monitor(self.h, 1, text.encode(), HEARD_BONUS)

    def step(self, n: int, commit_from: int, ev_thresh: float,
             force_cut: int = -1) -> _Reading | None:
        cut = self._lib.itila2_stream_step(
            self.h, self.env.ctypes.data_as(P), n, commit_from, ev_thresh, self._stable, self._live,
            TEXT_LEN, ct.byref(self._last), ct.byref(self._ev), force_cut)
        if cut < 0:
            return None
        wpm = self._lib.itila_get_wpm(self.h)
        return _Reading(cut, self._stable.value.decode(errors='replace').strip(),
                        self._live.value.decode(errors='replace').strip(),
                        self._last.value if self._last.value >= 0 else cut,
                        self._ev.value if math.isfinite(self._ev.value) else None,
                        self._lib.itila_get_last_cost(self.h), wpm if wpm > 0 else None)

    def free(self) -> None:
        if self.h:
            self._lib.itila_free(self.h)
        self.h = None


def shown(text: str, ev: float | None) -> bool:
    """Whether a committed stretch shows: CW evidence of its own, more for a lone E or T."""
    if ev is None:
        return True
    return ev >= (LONE_EV_MIN if text in ("E", "T") else SEG_EV_MIN)


class Monitor:
    """One monitor on one scanner.  feed(now) after each IQ block; words collects the commits."""

    def __init__(self, sc_lib: ct.CDLL, sc_handle: int, dec_lib: ct.CDLL, ev_thresh: float) -> None:
        if not _bind(sc_lib, dec_lib):
            raise RuntimeError("no monitor in these libraries (itila2, exp/click-decode)")
        self._sc_lib, self._sc, self._dec_lib = sc_lib, sc_handle, dec_lib
        self._ev = ev_thresh
        self._arms: list[_Arm] = []
        self.known: frozenset[str] = frozenset()   # the hub's known calls: an arm's call checks
        self.khz: float | None = None
        self.seeded_s = 0.0         # history it opened with, from a bin on the same signal
        self.live = ""
        self.words: list[Word] = []
        self._commit = 0            # samples since the monitor opened, up to the last cut
        self._next_step = 0.0
        self._passes = 0            # steps in a row the window passed the evidence test

    def open(self, khz: float) -> bool:
        self.close()
        if not self._sc_lib.itila_sc_monitor_open(self._sc, khz * 1000.0):
            return False
        self._arms = [_Arm(self._dec_lib, lpf) for _, lpf in ARMS]
        if not all(a.h for a in self._arms):
            self.close(scanner_gone=False, opened=True)
            return False
        self.khz, self.live, self._commit, self._next_step, self._passes = khz, "", 0, 0.0, 0
        end = ct.c_longlong(0)
        self.seeded_s = self._sc_lib.itila_sc_monitor_read(self._sc, None, None, 1 << 30,
                                                           ct.byref(end), None) / RATE_HZ
        return True

    def close(self, scanner_gone: bool = False, opened: bool = False) -> None:
        """scanner_gone: the scanner was freed, its monitor bin with it; free only the decoders."""
        if (self.khz is not None or opened) and not scanner_gone:
            self._sc_lib.itila_sc_monitor_close(self._sc)
        for a in self._arms:
            a.free()
        self._arms, self.khz = [], None

    def heard(self, calls: Iterable[str]) -> None:
        for a in self._arms:
            a.words(calls)

    @property
    def scanner(self) -> int:
        return self._sc

    def feed(self, now: float) -> list[Word]:
        """A step when STEP_S of audio has passed; returns the words it committed."""
        if not self._arms or now < self._next_step:
            return []
        self._next_step = now + STEP_S
        wide, narrow = self._arms
        end, afc = ct.c_longlong(0), ct.c_double(0.0)
        n = self._sc_lib.itila_sc_monitor_read(self._sc, narrow.env.ctypes.data_as(P),
                                               wide.env.ctypes.data_as(P), len(wide.env),
                                               ct.byref(end), ct.byref(afc))
        if n < RATE_HZ:
            return []
        start = end.value - n
        commit_from = max(0, self._commit - start)
        w = wide.step(n, commit_from, self._ev)
        if w is None:
            self._passes, self.live = 0, ""
            self._commit = max(self._commit, end.value - int(HOLD_BACK_S * RATE_HZ))
            return []
        self._passes += 1
        if self._passes < HOLD_STEPS:
            return []               # the commit waits: the first word comes once it holds
        nr = narrow.step(n, commit_from, self._ev, force_cut=w.cut)   # the same stretch
        best, other, arm = w, nr, ARMS[0][0]
        if nr is not None and narrow_wins(w.text, w.cost, nr.text, nr.cost, self.known):
            best, other, arm = nr, w, ARMS[1][0]
        self._commit = start + w.cut
        self.live = best.live
        if not best.text:
            return []
        word = Word(now - (n - best.last) / RATE_HZ, now, best.wpm, best.text, best.ev,
                    shown(best.text, best.ev), arm, other.text if other else "")
        self.words.append(word)
        return [word]


@dataclass
class Step:
    khz: float
    words: list[Word] = field(default_factory=list)
    live: str = ""
    text: str = ""            # what shows: the kept words, split calls joined
    back: int = 0             # words of the text shown before that this text replaces (K | F9VV)


LEAD_PROSIGNS = ("BK", "BT")          # run into the next word: no word starts so (KNOW, SKILL do)
TRAIL_PROSIGNS = ("BK", "BT", "KN")   # run into the last: none ends so (YEAR, DESK end in AR, SK)
QSO_WORDS = frozenset({"5NN", "599", "HR", "UR", "TU", "ES", "DE", "GM", "GA", "GE", "RR",
                       "FB", "73", "72", "OP", "QTH", "RST", "BK", "NAME", "TNX"})   # no call piece
CALL_SHAPE = re.compile(r"^(?:[A-Z]{1,2}|[A-Z]\d|\d[A-Z])\d[A-Z]{1,4}$")   # Web's CALL_RE


def split_prosigns(words: list[str]) -> list[str]:
    """A prosign run into a word split off it (5NN5NNBK, BKTUJIM, LakeDog 11-26); a call-shaped
    word keeps its letters (K1BK)."""
    out: list[str] = []
    for w in words:
        head: list[str] = []
        tail: list[str] = []
        while not CALL_SHAPE.match(w) and len(w) > 3 and w[:2] in LEAD_PROSIGNS:
            head.append(w[:2])
            w = w[2:]
        while not CALL_SHAPE.match(w) and len(w) > 3 and (
                w[-2:] in TRAIL_PROSIGNS        # AR after a number or QSO word: CLEAR stays
                or (w[-2:] == "AR"
                    and (any(c.isdigit() for c in w[:-2]) or w[:-2] in QSO_WORDS))):
            tail.insert(0, w[-2:])
            w = w[:-2]
        out += head + [w] + tail
    return out


JOIN_PIECES = 4        # a call split by fades into up to this many pieces (DEN 9 F GC: N9FGC)
HEARD_KHZ = 0.15       # a window this close to the pick is the same signal (bin snap and AFC)
JOIN_BACK_S = 3.0      # a piece joins words shown up to this long before (K | F9VV: KF9VV)


def _one_off(a: str, b: str) -> bool:
    """One substitution, insertion or deletion apart ('?' is a character like any other)."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:k] + long_[k + 1:] == short for k in range(len(long_)))


class CallJoin:
    """Split calls joined again: pieces in a row that make a known call, the first not a known
    call itself (KM7A Z: KM7AZ; DEN 9 F GC: DE N9FGC, LakeDog 11-26)."""

    def __init__(self, known: Iterable[str]) -> None:
        self.known = frozenset(c.strip().upper() for c in known if c.strip())

    def _call(self, joined: str) -> list[str] | None:
        if joined in self.known:
            return [joined]
        if joined.startswith("DE") and joined[2:] in self.known:
            return ["DE", joined[2:]]
        return None

    def join(self, words: list[str]) -> list[str]:
        out, i = [], 0
        while i < len(words):
            got, j = None, i + 1
            if words[i] not in self.known:
                for j in range(min(len(words), i + JOIN_PIECES), i + 1, -1):
                    if QSO_WORDS.isdisjoint(words[i + 1:j]) and words[i] not in QSO_WORDS - {"DE"} \
                            and (got := self._call("".join(words[i:j]))):
                        break     # 5NN HR is not 5NNHR, a junk call on the RBN list
            if got:
                out += got
                i = j
            else:
                out.append(words[i])
                i += 1
        return out

    def fix(self, words: list[str], heard: Iterable[str]) -> list[str]:
        """A call-like word (with a digit) one character off a call this monitor already showed
        is that call: AC5? and AC5G after DE AC5P (11-26).  A known call stays as it is."""
        heard = list(heard)
        out = []
        for w in words:
            near = [c for c in heard if w not in self.known and any(ch.isdigit() for ch in w)
                    and w != c and _one_off(w, c)]
            out.append(near[0] if len(near) == 1 else w)
        return out


Scanner = tuple[ct.CDLL, int, float]   # (scanner library, handle, ev_thresh) of one band


def _addr(h: object) -> int | None:
    """A scanner handle as an address: SparkGap holds a c_void_p, the tests an int."""
    v = getattr(h, "value", h)
    return v if isinstance(v, int) else None


class MonitorHub:
    """The one monitor of a process.  request() from any thread (the feed's loop); step() on the
    thread that owns the scanners, which also opens, moves and closes the monitor there."""

    def __init__(self, dec_lib: ct.CDLL) -> None:
        self._dec_lib = dec_lib
        self._want: tuple[float | None] | None = None   # a request not yet applied
        self._want_lock = threading.Lock()     # request() and step() run on different threads
        self.mon: Monitor | None = None
        self._live = ""
        self.joiner: CallJoin | None = None    # set_known(): the calls split calls join into
        self._tail: list[str] = []             # the last words shown, which a piece may join
        self._tail_at = 0.0
        self._heard: set[str] = set()          # known calls shown since the monitor opened
        self._windows: collections.deque[tuple[float, str]] = collections.deque(maxlen=200)

    def heard_window(self, khz: float, text: str) -> None:
        """A decoded window (any thread): its known calls near the pick join the calls heard, so
        WA2UIA shows as WA2USA, which the windows copied (11-26)."""
        self._windows.append((khz, text))

    def _take_windows(self, khz: float) -> None:
        while self._windows:
            k, text = self._windows.popleft()
            if self.joiner is not None and abs(k - khz) <= HEARD_KHZ:
                self._heard.update(w for w in text.split() if w in self.joiner.known)

    def set_known(self, calls: Iterable[str]) -> None:
        self.joiner = CallJoin(calls)

    def request(self, khz: float | None) -> None:
        with self._want_lock:
            self._want = (khz,)

    def step(self, scanners: list[Scanner], now: float) -> Step | None:
        """Apply the latest request, then step the monitor; a Step when it has new text."""
        live = {_addr(h) for _, h, _ in scanners}
        if self.mon is not None and _addr(self.mon.scanner) not in live:
            log.info("MONITOR %.2f kHz: band closed", self.mon.khz or 0.0)
            self.mon.close(scanner_gone=True)
            self.mon = None
        with self._want_lock:     # a pick made while this one is taken is kept for the next step
            want = self._want
            if want is not None and (want[0] is None or scanners):   # an open waits for a scanner
                self._want = None
            else:
                want = None
        if want is not None:
            self._move(want[0], scanners)
        if self.mon is None or self.mon.khz is None:
            self._windows.clear()
            return None
        heard = len(self._heard)
        self._take_windows(self.mon.khz)
        if len(self._heard) != heard:
            self.mon.heard(self._heard)
        words = self.mon.feed(now)
        heard = len(self._heard)
        back, text = self._shown([w.text for w in words if w.kept], now)
        if len(self._heard) != heard:
            self.mon.heard(self._heard)            # the decoder's prior, from the next step
        if not text and self.mon.live == self._live:
            return Step(self.mon.khz, words) if words else None
        self._live = self.mon.live
        return Step(self.mon.khz, words, self.mon.live, text, back)

    def _shown(self, texts: list[str], now: float) -> tuple[int, str]:
        """What to show from this step's kept commits at once, and how many of the last words
        shown it replaces: a piece that completes a call rewrites them (K | F9VV: KF9VV)."""
        words = split_prosigns(" ".join(texts).split())
        if self.joiner is None or not words:
            return 0, " ".join(words)
        tail = self._tail if now - self._tail_at <= JOIN_BACK_S else []
        out = self.joiner.fix(self.joiner.join(tail + words), self._heard)
        self._heard.update(w for w in out if w in self.joiner.known)
        same = 0
        while same < min(len(tail), len(out)) and out[same] == tail[same]:
            same += 1
        self._tail, self._tail_at = out[-(JOIN_PIECES - 1):], now
        return len(tail) - same, " ".join(out[same:])

    def close(self) -> None:
        self._move(None, [])

    def _move(self, khz: float | None, scanners: list[Scanner]) -> None:
        if self.mon is not None:
            log.info("MONITOR %.2f kHz: closed", self.mon.khz or 0.0)
            self.mon.close()
            self.mon = None
        self._live = ""
        self._tail, self._heard = [], set()
        if khz is None:
            return
        for sc_lib, sc_h, ev in scanners:
            try:
                mon = Monitor(sc_lib, sc_h, self._dec_lib, ev)
            except RuntimeError as e:
                log.warning("MONITOR %.2f kHz: %s", khz, e)
                return
            if mon.open(khz):
                mon.known = self.joiner.known if self.joiner else frozenset()
                log.info("MONITOR %.2f kHz: open, %.1f s from its bin", khz, mon.seeded_s)
                self.mon = mon
                return
        log.info("MONITOR %.2f kHz: on no band", khz)
