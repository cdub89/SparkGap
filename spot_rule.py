"""Repeat-evidence spot rule (config spot_rule: "repeat"; cdub89/SparkGap#8 SP2).

A call is spotted when, within the reputation horizon:
  1. it is the first callsign within LOOKAHEAD tokens after CQ or TEST
     (CQ CWT K0TQ, CQ POTA DE WX7V), or after DE when CQ or TEST came earlier with no
     callsign between and the call is sent twice in the window (CQ SKCC DE K4DH K4DH);
  2. that happens in at least tier(call) decode windows (2, 3 or 4 by patt3ch.lst);
  3. near-miss copies count toward it: a call one character off a call that has
     at least twice its windows on the same frequency is counted as that call, and so is
     a truncated call (KM7E, two or more letters lost) when a call it begins (KM7EJE)
     reaches its own tier there; a truncated call is not spotted while such a call is
     being heard; merge targets must have been decoded within MERGE_RECENT_S;
  4. a call followed by a name and a number or state is a caller being sent the
     exchange, not a runner, and does not count.
One spot per call per SPOT_HOLD_S unless it moves more than SPOT_MOVE_KHZ.

Matches the reference, WX7V/5 (CW Skimmer at validation Normal, no Master.dta, through
the Aggregator's CQ filter): patt3ch tiers and repeats, no SCP check (8 of the 12 calls
it sent to RBN 2026-10-06 18:21-19:02Z are not in MASTER.SCP). CWT alone is not a
keyword until a contest run shows CW Skimmer tags "TU CWT <call>" as CQ.

A window is one decode window of one bin; both LPF paths of a window count once.
"""

import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

KEYWORDS = frozenset({"CQ", "TEST"})
LOOKAHEAD = 3                 # tokens after a keyword searched for the sender's call
DE_LOOKBACK = 6               # tokens before DE searched for CQ or TEST
SAME_FREQ_KHZ = 0.3           # near-miss copies must share a frequency within this
MERGE_RATIO = 2               # the real call needs this many times the copy's windows
TRUNC_MIN = 3                 # shortest truncated call merged into a longer one (K4N)
MERGE_RECENT_S = 300.0        # a merge target decoded longer ago than this is another station
_GLUED = frozenset({"KN", "AR", "BK", "SK", "TU", "DE", "BT", "AS", "KA", "VA"})  # AB0CDKN
_NOISE = frozenset("EISHT5")  # an extension of only these is dit/dah noise, not lost letters
SPOT_HOLD_S = 600.0           # one spot per call per 10 minutes...
SPOT_MOVE_KHZ = 2.0           # ...unless it moves more than this
REPUTATION_S = 3600.0         # windows older than this are forgotten

CALL_RE = re.compile(r"^(?:[A-Z]{1,2}|[0-9][A-Z])[0-9]{1,4}[A-Z]{1,6}$")
_SPLIT_RE = re.compile(r"[^A-Z0-9?]+")
_MERGED_PREFIXES = ("CQ", "TEST", "CWT", "SST", "MST", "DE")
_TRIGGER_WORDS = ("CQ", "TEST", "CWT", "SST", "MST")
_STOP_WORDS = frozenset({"TU", "DE", "CQ", "TEST", "CWT", "SST", "MST", "QRZ", "AGN",
                         "NR", "TNX", "GL", "BK"})


def _split_merged(tok: str) -> list[str]:
    """CQK1ABC -> CQ K1ABC, DEW1AW -> DE W1AW (decoder dropped the word gap)."""
    out: list[str] = []
    rest = tok
    while rest:
        for pre in _MERGED_PREFIXES:
            if (rest.startswith(pre) and len(rest) > len(pre)
                    and re.match(r"[A-Z0-9]{1,2}\d", rest[len(pre):])):
                out.append(pre)
                rest = rest[len(pre):]
                break
        else:
            out.append(rest)
            rest = ""
    return out


def tokens(text: str) -> list[str]:
    """Upper-case word tokens of decoded text, merged keyword prefixes split off."""
    out: list[str] = []
    for t in _SPLIT_RE.split(text.upper()):
        if t:
            out.extend(_split_merged(t))
    return out


def _one_sub_same_kind(a: str, b: str) -> bool:
    """One substitution, letter for letter or digit for digit."""
    if len(a) != len(b):
        return False
    diff = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    return len(diff) == 1 and diff[0][0].isalpha() == diff[0][1].isalpha()


def _is_trigger_like(t: str) -> bool:
    return t in _TRIGGER_WORDS or any(_one_sub_same_kind(t, w) for w in ("CWT", "TEST"))


def sender(toks: list[str], i: int) -> int | None:
    """Index of the first callsign within LOOKAHEAD tokens after keyword toks[i]."""
    for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(toks))):
        if CALL_RE.match(toks[j]):
            return j
    return None


def is_exchange(toks: list[str], j: int) -> bool:
    """toks[j] is followed by NAME then a number or 2-letter state: a caller being sent
    the exchange. Repeats of the call and 1-character or '?' tokens before NAME are skipped."""
    k = j + 1
    while k < len(toks) and (toks[k] == toks[j] or len(toks[k]) <= 1 or "?" in toks[k]):
        k += 1
    if k + 1 >= len(toks):
        return False
    name, num = toks[k], toks[k + 1]
    return (name.isalpha() and 2 <= len(name) <= 6 and name not in _STOP_WORDS
            and not _is_trigger_like(name)
            and (any(ch.isdigit() for ch in num) or (num.isalpha() and len(num) == 2)))


def _cq_before(toks: list[str], i: int) -> bool:
    """CQ or TEST within DE_LOOKBACK tokens before toks[i], with no callsign between."""
    for k in range(i - 1, max(i - 1 - DE_LOOKBACK, -1), -1):
        if CALL_RE.match(toks[k].replace("?", "")):
            return False
        if toks[k] in KEYWORDS:
            return True
    return False


def runner_calls(text: str) -> list[str]:
    """Calls this text puts right after CQ or TEST (rules 1 and 4)."""
    toks = tokens(text)
    out = []
    for i, t in enumerate(toks):
        de = t == "DE" and _cq_before(toks, i)
        if t in KEYWORDS or de:
            j = sender(toks, i)
            if j is not None and de and toks.count(toks[j]) < 2:
                continue
            if j is not None and not is_exchange(toks, j) and toks[j] not in out:
                out.append(toks[j])
    return out


def _lev1(a: str, b: str) -> bool:
    """True when a and b are exactly one edit apart."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


@dataclass
class _Sightings:
    wins: dict[int, float] = field(default_factory=dict)      # window -> freq, any context
    keyed: dict[int, float] = field(default_factory=dict)     # window -> freq, after a keyword
    bins: Counter[int] = field(default_factory=Counter)       # round(freq x 10) -> windows
    last: float = 0.0                                          # time of the latest window


def _bin(freq_khz: float) -> int:
    return round(freq_khz * 10)


_NEAR_BINS = range(-round(SAME_FREQ_KHZ * 10), round(SAME_FREQ_KHZ * 10) + 1)


class RepeatSpotRule:
    """Feed every decode window; returns the spots the window completes."""

    def __init__(self, tier: Callable[[str], int]) -> None:
        self.tier = tier
        self._calls: dict[str, _Sightings] = {}
        self._index: dict[str, set[str]] = defaultdict(set)   # edit-1 and prefix keys -> calls
        self._log: deque[tuple[float, str, int]] = deque()    # (time, call, window), oldest first
        self._window_ids: dict[tuple[int, int], tuple[int, float]] = {}   # -> (id, first seen)
        self._next_window = 0
        self._last_spot: dict[str, tuple[float, float]] = {}  # call -> (time, freq)
        self._last_prune = 0.0
        self._now = 0.0

    def window_id(self, bin_id: int, window_key: int, now: float) -> int:
        """Stable id for one bin's decode window (both LPF paths map to the same id)."""
        key = (bin_id, window_key)
        if key not in self._window_ids:
            self._window_ids[key] = (self._next_window, now)
            self._next_window += 1
        return self._window_ids[key][0]

    def feed(self, window: int, freq_khz: float, text: str, now: float) -> list[tuple[str, float]]:
        """Record one decoded text of a window; return [(call, freq_khz)] to spot now."""
        self._expire(now)
        self._now = now
        toks = tokens(text)
        for t in toks:
            if CALL_RE.match(t):
                s = self._calls.get(t)
                if s is None:
                    s = self._calls[t] = _Sightings()
                    for k in self._edit_keys(t):
                        self._index[k].add(t)
                s.last = now
                if window not in s.wins:
                    s.wins[window] = freq_khz
                    s.bins[_bin(freq_khz)] += 1
                    self._log.append((now, t, window))
        spots = []
        for call in runner_calls(text):
            self._calls[call].keyed.setdefault(window, freq_khz)
            target = self._dominant(call) or call
            if not self._near(target, freq_khz) or self._longer(target):
                continue
            if self._keyed_windows(target) >= self.tier(target):
                last = self._last_spot.get(target)
                if (last is None or now - last[0] >= SPOT_HOLD_S
                        or abs(freq_khz - last[1]) > SPOT_MOVE_KHZ):
                    self._last_spot[target] = (now, freq_khz)
                    spots.append((target, freq_khz))
        return spots

    @staticmethod
    def _edit_keys(call: str) -> Iterable[str]:
        yield call
        for i in range(len(call)):
            yield call[:i] + "*" + call[i + 1:]
            yield call[:i] + call[i + 1:]
        for k in range(TRUNC_MIN, len(call) - 1):
            yield "^" + call[:k]

    def _neighbours(self, call: str) -> set[str]:
        near: set[str] = set()
        for k in self._edit_keys(call):
            near |= self._index.get(k, set())
        return {c for c in near if c != call and _lev1(c, call)}

    def _longer(self, call: str) -> set[str]:
        """Recent calls on call's frequency that begin with call plus two or more
        characters, not a glued prosign: what a truncated call may be a copy of."""
        return {d for d in self._index.get("^" + call, set())
                if d[len(call):] not in _GLUED and not set(d[len(call):]) <= _NOISE
                and self._recent(d) and self._shares_freq(call, d)}

    def _prefixes(self, call: str) -> set[str]:
        """Decoded calls that call begins with: its possible truncated copies."""
        return {call[:k] for k in range(TRUNC_MIN, len(call) - 1) if call[:k] in self._calls}

    def _recent(self, call: str) -> bool:
        return self._calls[call].last >= self._now - MERGE_RECENT_S

    def _n(self, call: str) -> int:
        s = self._calls.get(call)
        return len(s.wins) if s else 0

    def _near(self, call: str, freq_khz: float) -> bool:
        """call was itself decoded within SAME_FREQ_KHZ of freq_khz."""
        s = self._calls.get(call)
        return bool(s) and any(s.bins.get(_bin(freq_khz) + k) for k in _NEAR_BINS)

    def _shares_freq(self, a: str, b: str) -> bool:
        return any(self._near(b, f / 10) for f in self._calls[a].bins)

    def _dominant(self, call: str) -> str | None:
        """The call this call is a near-miss or truncated copy of (rule 3), or None. A runner
        is decoded in nearly every window, so it is never outnumbered 2:1 by a caller one
        character away."""
        best = None
        longer = self._longer(call)
        for d in self._neighbours(call) | longer:
            if d in longer:
                ok = self._n(d) >= self.tier(d) and self._n(d) > self._n(call)
            else:
                ok = self._n(d) >= MERGE_RATIO * self._n(call)
            if (ok and self._recent(d) and self._shares_freq(call, d)
                    and (best is None or (self._n(d), best) > (self._n(best), d))):
                best = d
        return best

    def _keyed_windows(self, target: str) -> int:
        """Windows with target after a keyword: its own anywhere, plus its near-miss
        copies' only where target itself was decoded (same frequency)."""
        windows = set(self._calls[target].keyed)
        for c in self._neighbours(target) | self._prefixes(target):
            if self._dominant(c) == target:
                windows |= {w for w, f in self._calls[c].keyed.items() if self._near(target, f)}
        return len(windows)

    def _expire(self, now: float) -> None:
        cutoff = now - REPUTATION_S
        while self._log and self._log[0][0] < cutoff:
            _, call, window = self._log.popleft()
            s = self._calls.get(call)
            if s is None or window not in s.wins:
                continue
            b = _bin(s.wins.pop(window))
            s.keyed.pop(window, None)
            s.bins[b] -= 1
            if s.bins[b] <= 0:
                del s.bins[b]
            if not s.wins:
                del self._calls[call]
                for k in self._edit_keys(call):
                    self._index[k].discard(call)
                    if not self._index[k]:
                        del self._index[k]
        if now - self._last_prune > 60.0:
            self._last_prune = now
            held = now - SPOT_HOLD_S
            self._last_spot = {c: v for c, v in self._last_spot.items() if v[0] >= held}
            self._window_ids = {k: v for k, v in self._window_ids.items() if v[1] >= cutoff}
