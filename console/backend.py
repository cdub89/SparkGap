"""SparkGap Console backend: an Instance per feed (WebSocket) and the waterfall QML draws."""

from __future__ import annotations

import collections
import html
import json
import math
import os
import random
import re
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import (
    Property,
    QObject,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtNetwork import QNetworkRequest
from PySide6.QtQml import QmlElement
from PySide6.QtQuick import QQuickPaintedItem
from PySide6.QtWebSockets import QWebSocket, QWebSocketProtocol

PROTOCOL = "sparkgap.v1"
FEED_VERSION = 1
MAX_BINS = 16_384  # a row wider than this is not a spectrum the feed sends
DEFAULT_PORT = 7320
HAM_BANDS = (("160m", 1800, 2000), ("80m", 3500, 4000), ("60m", 5330, 5410),
             ("40m", 7000, 7300), ("30m", 10100, 10150), ("20m", 14000, 14350),
             ("17m", 18068, 18168), ("15m", 21000, 21450), ("12m", 24890, 24990),
             ("10m", 28000, 29700))  # kHz, to name a band given as a frequency
SCAN_PORTS = range(DEFAULT_PORT, DEFAULT_PORT + 20)  # tried when a server is added without a port
SCAN_TIMEOUT_S = 3.0
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # join codes, as the feed server accepts them
CODE_LENGTH = 6
SPECTRUM_HEADER = struct.Struct("<IddffH")
SPECTRUM_HZ = 60  # rows asked of the server (one pixel column each)
HISTORY_COLS = 2_400  # waterfall columns kept per instance
PLAYOUT_MARGIN_S = 0.20  # jitter buffer: rows are shown this long after they arrive
PENDING_MAX = 120  # rows waiting to be drawn; a hidden window draws none, so older ones go
SPOT_KEEP_S = 600
SIGNAL_KEEP_S = 180
RECONNECT_S = (1, 2, 5, 10, 20)
LOCKOUT_S = 600  # the feed refuses an address this long after repeated wrong join codes
DB_FLOOR = 4.0  # dB over the noise floor drawn as background
DB_TOP = 32.0  # top of the colour scale
TRACE_MIN_PX = 5  # a signal is drawn at least this tall
FRAME_LOG = bool(os.environ.get("SPARKGAP_FRAME_LOG"))  # print frame timing every 1200 frames
FREQ_MATCH_KHZ = 0.15  # a window belongs to a call within this distance
SEARCH_MAX = 8  # matches listed under the search box
NO_FIT = 999.0  # itila2's timing cost when a window has too little structure to score
DETAIL_MATCH_KHZ = 0.06  # the decoded-text panel shows one scanner bin (bins sit ~100 Hz apart)
CALL_RE = re.compile(r"^(?:[A-Z]{1,2}|[A-Z]\d|\d[A-Z])\d[A-Z]{1,4}$")  # base call shape
HEARD_S = 90.0  # a decoded signal keeps its bubble this long after its last window
GUESS_WINDOWS = 4  # recent windows searched for a repeated call
PEAK_LOOK_S = 10.0  # a click snaps to the strongest trace seen this recently
PEAK_MIN_DB = 8.0
TRACE_DUTY = 0.15  # share of time a trace must be up to earn a bubble (noise rarely is)
DEMO_URL = "demo"

# demo: made-up signals, the same as web/app.js
MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....",
    "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.",
    "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.", "/": "-..-.",
}
# (kHz, wpm, SNR dB, sent text, spotted); no text: a carrier with nothing decoded
DEMO_SIGNALS = [
    (7016.8, 21, 9, "TU 5NN TX", False),
    (7019.3, 25, 14, "CQ CQ DE N3AD N3AD K", True),
    (7021.2, 22, 18, "CQ CQ DE AF4PX AF4PX K", True),
    (7024.6, 26, 23, "CQ CQ DE AC5P AC5P K", False),
    (7027.9, 30, 16, "CQ NA4J NA4J TEST", True),
    (7031.3, 18, 27, "CQ CQ CQ DE K4WLO K4WLO K", True),
    (7033.0, 32, 12, "TEST K3WW K3WW", True),
    (7036.4, 20, 20, "CQ CQ DE WB5NHH WB5NHH PSE K", False),
    (7039.8, 24, 15, "CQ DE W3RJ W3RJ K", True),
    (7041.5, 27, 11, "CQ N4ZZ N4ZZ", False),
    (7046.2, 16, 19, "GM OM UR RST 579 579 NAME BOB BOB QTH OH", False),
    (7052.7, 0, 13, "", False),
    (7055.6, 19, 21, "CQ SKCC DE KD8QWX KD8QWX K", True),
    (7061.0, 23, 8, "R R TU 73 EE", False),
]
DEMO_BAND = {"name": "40m", "center_khz": 7048.0, "span_khz": 96.0}
DEMO_BINS = 2048
DEMO_SUBSTEPS = 4  # keying sampled within each row, so edges blend
DEMO_DB_UNIT = 0.25


class Instance(QObject):
    """One SparkGap feed: connection, decoded state and the waterfall ring buffer."""

    changed = Signal()
    statusChanged = Signal()
    ladderChanged = Signal()
    detailChanged = Signal()
    stateChanged = Signal()
    rowsReady = Signal()

    def __init__(self, url: str, token: str = "", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url = url
        self._token = token
        self.source: Demo | None = None
        self._state = "connecting"
        self._error = ""
        self.hello: dict[str, Any] = {}
        self._status: dict[str, Any] = {}
        self.windows: list[dict[str, Any]] = []
        self.spots: dict[str, dict[str, Any]] = {}  # call -> latest spot
        self.signals: dict[float, dict[str, Any]] = {}  # khz -> latest window meta
        self.recent: dict[float, collections.deque[str]] = {}  # khz -> last texts
        self._heard: list[tuple[float, bool]] = []
        self._sel_khz: float | None = None
        self._sel_call = ""
        self._since = 0.0  # when this console last connected (status page)
        self._reconnects = 0
        self._band = 0  # which of hello's bands the waterfall shows; a SparkGap may run several
        self.go_khz: float | None = None  # match on another band: centred once its rows arrive
        self._accent = "#f3a640"
        # spectrum
        self.first_hz = 0.0
        self.hz_per_bin = 1.0
        self.n_bins = 0
        self.ring: np.ndarray | None = None  # (n_bins, HISTORY_COLS) u8, dB / db_unit
        self.db_unit = 0.25
        self.head = 0  # next column to write
        self.cols = 0  # columns written so far
        self.total = 0  # rows ever written, for the waterfall's incremental colouring
        self.pending: list[tuple[float, np.ndarray]] = []  # (play time, row)
        self.clock_offset: float | None = None  # arrival - server utc, smallest seen
        self.row_period = 1.0 / SPECTRUM_HZ
        self.last_play = 0.0
        self.frac = 0.0

        self.ws = QWebSocket()
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_text)
        self.ws.binaryMessageReceived.connect(self._on_binary)
        self.ws.errorOccurred.connect(self._on_error)
        self.retry = 0
        self.closed = False
        self._opened = False  # this attempt reached the feed
        self.refused = ""  # "code": wrong code, not retried; "lockout": retried after LOCKOUT_S
        self.retry_timer = QTimer(self, singleShot=True)
        self.retry_timer.timeout.connect(self.open)
        self.ladder_timer = QTimer(self, interval=1000)
        self.ladder_timer.timeout.connect(self._expire)
        self.ladder_timer.start()

    # ---- connection ----

    @Slot()
    def open(self) -> None:
        self.refused = ""
        if self.is_demo:
            self.source = Demo(self)
            self._set_state("live")
            return
        self._set_state("connecting")
        self._opened = False
        self.ws.open(open_request(self._url, self._token))

    @property
    def feed(self) -> tuple[str, str]:
        """(url, token) as a plain pair for Python callers; url is also a QML property."""
        return self._url, self._token

    def close(self) -> None:
        self.closed = True
        self.retry_timer.stop()
        if self.source:
            self.source.stop()
        self.ws.close()

    def _on_connected(self) -> None:
        if self._since:
            self._reconnects += 1
        self._since = time.time()
        self.retry = 0
        self._error = ""
        self._opened = True
        self.ws.sendTextMessage(json.dumps({"type": "subscribe", "spectrum_hz": SPECTRUM_HZ}))
        self._set_state("live")

    def _on_disconnected(self) -> None:
        if self.closed or self.refused:
            return
        code = self.ws.closeCode()
        if code == QWebSocketProtocol.CloseCode.CloseCodePolicyViolated:
            self._error = "dropped by the server (too slow)"
        elif self._opened:
            self._error = ""
        delay = RECONNECT_S[min(self.retry, len(RECONNECT_S) - 1)]
        self.retry += 1
        self._set_state("dropped")
        self.retry_timer.start(delay * 1000)

    def _on_error(self, _err: Any) -> None:
        err = self.ws.errorString()
        self.refused = refusal(err)
        if self.refused == "code":
            err = "wrong join code"
        elif self.refused == "lockout":
            err = "locked out after wrong join codes; retrying in 10 min"
        elif "503" in err:
            err = "too many clients"
        else:
            # plain words as in SparkGap Web; a drop from a live feed shows none
            err = "" if self._opened else "no answer"
        self._error = err
        if self.refused:
            # a retry with the same code would only count towards a lockout
            self.retry_timer.stop()
            if self.refused == "lockout":
                self.retry_timer.start(LOCKOUT_S * 1000)
            self._set_state("refused")
        else:
            self.stateChanged.emit()

    def _set_state(self, s: str) -> None:
        self._state = s
        self.stateChanged.emit()

    # ---- messages ----

    def _on_text(self, text: str) -> None:
        try:
            m = json.loads(text)
        except ValueError:
            return
        kind = m.get("type")
        if kind == "hello":
            self.hello = m
            if not 0 <= self._band < len(self._bands()):
                self._band = 0
            self.windows, self.spots, self.signals, self.recent = [], {}, {}, {}
            self.changed.emit()
        elif kind == "snapshot":
            for w in m.get("windows", []):
                self._add_window(w)
            for s in m.get("spots", []):
                self.spots[s["call"]] = s
            if m.get("status"):
                self._status = m["status"]
                self.statusChanged.emit()
            self._relayout()
        elif kind == "window":
            self._add_window(m)
            if self._matches_selection(m["khz"]):
                self.detailChanged.emit()
        elif kind == "spot":
            first = m["call"] not in self.spots
            self.spots[m["call"]] = m
            if first:
                self._relayout()
            if self._matches_selection(m["khz"]):
                self.detailChanged.emit()  # HEARD becomes SPOTTED
        elif kind == "status":
            self._status = m
            self.statusChanged.emit()

    def _add_window(self, w: dict[str, Any]) -> None:
        # SparkGap decodes each window twice, from envelopes taken through a narrow (~100 Hz,
        # smoothed) and a wide (~200 Hz) channel filter, and sends the narrow reading first.
        # Show the wide reading on top: on leg 3 it held the RBN-confirmed call in 89% of pairs
        # against 57% (the lower cost picked right only 72% of the time).
        for prev in reversed(self.windows[-8:]):
            if (
                abs(prev["khz"] - w["khz"]) < 0.02
                and abs(prev["utc"] - w["utc"]) < 1.0
                and "alt" not in prev
            ):
                alt = {k: prev[k] for k in ("text", "cost", "wpm")}
                prev.update(w)
                prev["rate"], alt["rate"] = 200, 100
                # cost 999 is the decoder's "no decode" (too little structure to score): then the
                # narrow reading goes on top if it has a real fit (8P5A at 40 WPM in a CWT)
                if (prev.get("cost") or 0) >= NO_FIT and (alt.get("cost") or NO_FIT) < NO_FIT:
                    top = {k: alt[k] for k in ("text", "cost", "wpm", "rate")}
                    alt = {k: prev[k] for k in ("text", "cost", "wpm", "rate")}
                    prev.update(top)
                prev["alt"] = alt
                self.recent.setdefault(w["khz"], collections.deque(maxlen=GUESS_WINDOWS)).append(
                    w["text"]
                )
                return
        self.windows.append(w)
        del self.windows[:-3000]
        self.signals[w["khz"]] = w
        self.recent.setdefault(w["khz"], collections.deque(maxlen=GUESS_WINDOWS)).append(w["text"])

    def _on_binary(self, data: bytes) -> None:
        if len(data) < SPECTRUM_HEADER.size:
            return
        v, utc, first, hz, unit, n = SPECTRUM_HEADER.unpack_from(data)
        if v != FEED_VERSION or not 0 < n <= MAX_BINS or len(data) != SPECTRUM_HEADER.size + n:
            return
        if not all(math.isfinite(x) for x in (utc, first, hz, unit)) or hz <= 0 or unit <= 0:
            return
        bands = self._bands()
        if len(bands) > 1 and 0 <= self._band < len(bands):
            b = bands[self._band]
            if abs(first - (b["center_khz"] * 1000 - b["span_khz"] * 500)) > hz:
                return  # another band's row
        row = np.frombuffer(data, dtype=np.uint8, count=n, offset=SPECTRUM_HEADER.size)
        if (n, first, hz, unit) != (self.n_bins, self.first_hz, self.hz_per_bin, self.db_unit):
            self.first_hz, self.hz_per_bin, self.n_bins, self.db_unit = first, hz, n, unit
            self.ring = np.zeros((n, HISTORY_COLS), dtype=np.uint8)
            self.head = self.cols = self.total = 0
            self.pending.clear()
            self.clock_offset = None
            self.changed.emit()
        now = time.time()
        off = now - utc
        if self.clock_offset is None or off < self.clock_offset:
            self.clock_offset = off  # tracks server clock and network delay
        self.pending.append((utc + self.clock_offset + PLAYOUT_MARGIN_S, row.copy()))
        if len(self.pending) > PENDING_MAX:
            del self.pending[:-PENDING_MAX]

    def advance(self) -> bool:
        """Move due rows into the ring; returns True when the picture moved."""
        now = time.time()
        moved = False
        while self.pending and self.pending[0][0] <= now and self.ring is not None:
            t, row = self.pending.pop(0)
            if self.last_play:
                gap = t - self.last_play
                if 0 < gap < 1:
                    self.row_period += (gap - self.row_period) * 0.05
            self.last_play = t
            self.ring[:, self.head] = row
            self.head = (self.head + 1) % HISTORY_COLS
            self.cols = min(self.cols + 1, HISTORY_COLS)
            self.total += 1
            moved = True
        if len(self.pending) > 60:  # far behind (e.g. after a stall): skip ahead
            del self.pending[:-10]
        frac = 0.0
        if self.last_play:
            frac = max(0.0, min(1.0, (now - self.last_play) / self.row_period))
        if frac != self.frac:
            self.frac = frac
            moved = True
        return moved

    # ---- derived state for QML ----

    def _expire(self) -> None:
        self._refresh_heard()
        now = time.time()
        old_spots = [c for c, s in self.spots.items() if now - s["utc"] > SPOT_KEEP_S]
        old_sigs = [k for k, s in self.signals.items() if now - s["utc"] > SIGNAL_KEEP_S]
        for c in old_spots:
            del self.spots[c]
        for k in old_sigs:
            del self.signals[k]
            self.recent.pop(k, None)
        if old_spots or old_sigs:
            self._relayout()

    def _relayout(self) -> None:
        self.ladderChanged.emit()

    def _matches_selection(self, khz: float) -> bool:
        return self._sel_khz is not None and abs(khz - self._sel_khz) <= FREQ_MATCH_KHZ

    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state

    @Property(str, notify=stateChanged)
    def error(self) -> str:
        return self._error

    @Property(str, constant=True)
    def url(self) -> str:
        return self._url

    @property
    def is_demo(self) -> bool:
        return self._url == DEMO_URL

    demo = Property(bool, lambda self: self.is_demo, constant=True)

    def _bands(self) -> list[dict[str, Any]]:
        return self.hello.get("bands") or []

    @Property(list, notify=changed)
    def bandNames(self) -> list[str]:  # noqa: N802
        """One label per band, for the band picker."""
        return [band_label(b) for b in self._bands()]

    @Property(int, notify=changed)
    def band(self) -> int:
        return self._band

    @Slot(int)
    def setBand(self, n: int) -> None:  # noqa: N802
        """Show band n: the next row of it starts a fresh waterfall history."""
        if n == self._band or not 0 <= n < len(self._bands()):
            return
        self._band = n
        self.n_bins = 0
        self.ring = None
        self.pending.clear()
        self.head = self.cols = self.total = 0
        self._sel_khz, self._sel_call = None, ""
        self.changed.emit()
        self.detailChanged.emit()

    @Slot(float, result=int)
    def bandAt(self, khz: float) -> int:  # noqa: N802
        """The band holding khz, or -1."""
        return next((n for n, b in enumerate(self._bands())
                     if abs(khz - b["center_khz"]) <= b["span_khz"] / 2), -1)

    @Slot(float)
    def goTo(self, khz: float) -> None:  # noqa: N802
        """Centre on khz once its band's rows arrive (after setBand)."""
        self.go_khz = khz

    def _station(self) -> str:
        """Host and band from hello; empty until the first hello. The address is for Settings."""
        bands = self._bands()
        b = bands[self._band] if 0 <= self._band < len(bands) else bands[0] if bands else None
        band = band_label(b) if b else ""
        # a config named after its instance (lakehouse1.local.json) names the server, else the host
        cfg = Path(str((self.hello.get("run") or {}).get("config") or "")).name
        local = cfg.endswith(".local.json")
        name = cfg.removesuffix(".local.json") if local else self.hello.get("host")
        return " · ".join(p for p in (name, band) if p)

    station = Property(str, _station, notify=changed)

    @Property(str, notify=changed)
    def host(self) -> str:
        return str(self.hello.get("host") or "")

    @Property(str, notify=changed)
    def title(self) -> str:
        if self.is_demo:
            return "Demo"
        return self._station() or self._url.split("//")[-1].split("/")[0]

    @Property(dict, notify=changed)
    def info(self) -> dict[str, Any]:
        return self.hello

    @Property(dict, notify=statusChanged)
    def status(self) -> dict[str, Any]:
        return self._status

    @Property(float, notify=statusChanged)
    def since(self) -> float:
        """When this console last connected (UTC seconds), for the status page."""
        return self._since

    @Property(int, notify=statusChanged)
    def reconnects(self) -> int:
        return self._reconnects

    @Property(int, notify=statusChanged)
    def rowsTotal(self) -> int:  # noqa: N802
        """Waterfall rows drawn so far; the status page turns it into a rate."""
        return self.total

    def low_khz(self) -> float:
        return self.first_hz / 1000.0 if self.n_bins else 0.0

    def high_khz(self) -> float:
        return (self.first_hz + self.n_bins * self.hz_per_bin) / 1000.0 if self.n_bins else 0.0

    lowKhz = Property(float, low_khz, notify=changed)  # noqa: N815
    highKhz = Property(float, high_khz, notify=changed)  # noqa: N815

    @Slot(float, float, result="QVariantList")
    def ladder(self, view_lo: float, view_hi: float) -> list[dict[str, Any]]:
        """Calls (spots) and call-less active signals inside the view, lowest frequency last."""
        items: list[dict[str, Any]] = []
        spotted_khz = []
        for call, s in self.spots.items():
            if view_lo <= s["khz"] <= view_hi:
                items.append({"call": call, "khz": s["khz"], "wpm": s.get("wpm"), "spot": True})
                spotted_khz.append(s["khz"])
        # decoded but not spotted: a bubble with the call that repeats in its recent text
        now = time.time()
        for khz, w in self.signals.items():
            if not (view_lo <= khz <= view_hi) or now - w["utc"] > HEARD_S:
                continue
            if any(abs(khz - k) <= FREQ_MATCH_KHZ for k in spotted_khz):
                continue
            guess = self.guess_call(khz)
            if guess:
                items.append({"call": guess, "khz": khz, "wpm": w.get("wpm"), "spot": False})
                spotted_khz.append(khz)
        sel = self._sel_khz
        if sel is not None and view_lo <= sel <= view_hi:
            if all(abs(sel - k) > FREQ_MATCH_KHZ for k in spotted_khz):
                w = self.signals.get(sel, {})
                items.append({"call": "", "khz": sel, "wpm": w.get("wpm"), "spot": False})
        return sorted(items, key=lambda i: -i["khz"])

    def guess_call(self, khz: float) -> str:
        """The call-shaped word seen most often (at least twice) in khz's recent windows."""
        n: collections.Counter[str] = collections.Counter()
        for k, texts in self.recent.items():
            if abs(k - khz) <= FREQ_MATCH_KHZ:
                for text in texts:
                    for word in text.split():
                        base = max(word.split("/"), key=len)
                        if CALL_RE.match(base):
                            n[base] += 1
        best = n.most_common(1)
        return best[0][0] if best and best[0][1] >= 2 else ""

    @Slot(result=list)
    def bubbles(self) -> list[dict[str, Any]]:
        """Signals SparkGap is decoding, for the bubbles QML draws on the pane divider."""
        return [{"khz": k, "spot": s} for k, s in self._heard]

    def heard(self) -> list[tuple[float, bool]]:
        """(khz, spotted) for decoded signals with a trace or a call; refreshed each second."""
        return self._heard

    def _refresh_heard(self) -> None:
        now = time.time()
        spots = [s["khz"] for s in self.spots.values()]
        out = []
        for k, w in self.signals.items():
            if now - w["utc"] > HEARD_S:
                continue
            spotted = any(abs(k - s) <= FREQ_MATCH_KHZ for s in spots)
            if spotted or self._keyed(k) or self.guess_call(k):
                out.append((k, spotted))
        self._heard = out

    def _marked(self, text: str, call: str) -> str:
        """Decoded text as HTML with the call highlighted."""
        words = []
        for word in html.escape(text).split(" "):
            if call and word == call:
                word = f"<b><font color='{self._accent}'>{word}</font></b>"
            words.append(word)
        return " ".join(words)

    @Slot(str, result=list)
    def search(self, query: str) -> list[dict[str, Any]]:
        """Matches for the header search: a frequency (kHz or MHz), or calls containing the text,
        from spots and decoded windows; spotted first, then exact, then most recent."""
        q = query.strip().upper()
        if not q:
            return []
        v = float(q) if re.fullmatch(r"\d+\.?\d*|\.\d+", q) else None
        if v is not None:
            khz = v * 1000 if v < 100 else v
            return [{"call": "", "khz": khz, "ago": "", "spot": False, "label": f"{khz:.1f} kHz"}]
        found: dict[str, dict[str, Any]] = {}
        for call, s in self.spots.items():
            if q in call:
                found[call] = {"call": call, "khz": s["khz"], "utc": s["utc"], "spot": True}
        for w in reversed(self.windows[-3000:]):
            for word in w["text"].split():
                base = max(word.split("/"), key=len)
                if q in base and CALL_RE.match(base) and base not in found:
                    found[base] = {"call": base, "khz": w["khz"], "utc": w["utc"], "spot": False}
        now = time.time()
        out = sorted(found.values(), key=lambda m: (not m["spot"], m["call"] != q, now - m["utc"]))
        for m in out:
            ago = int(now - m.pop("utc"))
            m["ago"] = f"{ago}s" if ago < 120 else f"{ago // 60}m"
            m["label"] = m["call"]
        return out[:SEARCH_MAX]

    @Slot(float, str)
    def select(self, khz: float, call: str) -> None:
        self._sel_khz, self._sel_call = khz, call
        self.detailChanged.emit()

    @Slot(float, float, result=float)
    def nearestSignal(self, khz: float, tol_khz: float) -> float:  # noqa: N802
        """A decoded signal near khz if there is one, else the strongest trace near khz."""
        cands = [s["khz"] for s in self.spots.values()] + list(self.signals)
        if cands:
            best = min(cands, key=lambda k: abs(k - khz))
            if abs(best - khz) <= max(tol_khz, FREQ_MATCH_KHZ):
                return best
        peak = self._peak(khz, tol_khz)
        return peak[0] if peak else -1.0

    def _keyed(self, khz: float) -> bool:
        """A trace at khz above PEAK_MIN_DB for at least TRACE_DUTY of the last PEAK_LOOK_S."""
        if self.ring is None or not self.cols:
            return False
        n = min(self.cols, int(PEAK_LOOK_S / self.row_period))
        idx = (self.head - n + np.arange(n)) % self.ring.shape[1]
        b = int((khz * 1000 - self.first_hz) / self.hz_per_bin)
        sl = self.ring[max(0, b - 1) : b + 2, idx]
        if not sl.size:
            return False
        on = sl.max(axis=0).astype(float) * self.db_unit >= PEAK_MIN_DB
        return bool(on.mean() >= TRACE_DUTY)

    def _peak(self, khz: float, tol_khz: float) -> tuple[float, float] | None:
        """(khz, dB) of the strongest bin within tol_khz over the last PEAK_LOOK_S."""
        if self.ring is None or not self.cols:
            return None
        n = min(self.cols, int(PEAK_LOOK_S / self.row_period))
        idx = (self.head - n + np.arange(n)) % self.ring.shape[1]
        b0 = max(0, int(((khz - tol_khz) * 1000 - self.first_hz) / self.hz_per_bin))
        b1 = min(self.n_bins, math.ceil(((khz + tol_khz) * 1000 - self.first_hz) / self.hz_per_bin))
        if b1 <= b0:
            return None
        level = self.ring[b0:b1, idx].max(axis=1).astype(float) * self.db_unit
        k = int(np.argmax(level))
        if level[k] < PEAK_MIN_DB:
            return None
        return (self.first_hz + (b0 + k + 0.5) * self.hz_per_bin) / 1000.0, float(level[k])

    @Slot(str)
    def setAccent(self, color: str) -> None:  # noqa: N802
        self._accent = color
        self.detailChanged.emit()

    @Property(dict, notify=detailChanged)
    def detail(self) -> dict[str, Any]:
        if self._sel_khz is None:
            return {}
        k = self._sel_khz
        call = (
            self._sel_call
            or next((c for c, s in self.spots.items() if abs(s["khz"] - k) <= FREQ_MATCH_KHZ), "")
            or self.guess_call(k)
        )
        wins = [w for w in self.windows if abs(w["khz"] - k) <= DETAIL_MATCH_KHZ][-40:]
        last = wins[-1] if wins else {}
        rows = []
        for w in reversed(wins):
            alt = w.get("alt") or {}
            rows.append(
                {
                    "utc": time.strftime("%H:%M:%S", time.gmtime(w["utc"])),
                    "html": self._marked(w["text"], call),
                    "cost": w.get("cost"),
                    "rate": w.get("rate"),
                    "alt_html": self._marked(alt["text"], call) if alt else "",
                    "alt_cost": alt.get("cost"),
                    "alt_rate": alt.get("rate"),
                }
            )
        # speed and level of the call shown: the spot's own when spotted, else the newest window
        # that copied the call (the newest at this frequency may be noise after the station stopped)
        spot = self.spots.get(call) if call else None
        src = spot or next((w for w in reversed(wins) if call and call in w["text"].split()), last)
        snr = src.get("snr")
        if snr is None:  # no decoded text: take the level from the spectrum
            peak = self._peak(k, FREQ_MATCH_KHZ)
            snr = peak[1] if peak else None
        note = (
            ""
            if rows
            else "No decoded text at this frequency yet. SparkGap skips the FT8 and digital "
            "segments, and starts a decoder only on a steady signal above its SNR threshold. "
            "Text appears here if it does."
        )
        return {
            "khz": k,
            "spot_khz": spot["khz"] if spot else None,
            "call": call,
            "spotted": spot is not None,
            "wpm": src.get("wpm"),
            "snr": snr,
            "windows": rows,
            "note": note,
        }


def keying(text: str, wpm: float) -> list[tuple[int, float]]:
    """(on, seconds) for one sending of text, then a pause."""
    u, out = 1.2 / wpm, []
    for wi, word in enumerate(text.split(" ")):
        if wi:
            out.append((0, 7 * u))
        for ci, ch in enumerate(word):
            if ci:
                out.append((0, 3 * u))
            for ei, el in enumerate(MORSE.get(ch, "")):
                if ei:
                    out.append((0, u))
                out.append((1, u if el == "." else 3 * u))
    out.append((0, 2 + random.random() * 3))
    return out


def misread(text: str, rough: float) -> str:
    """A decoded reading of sent text: mostly right, sometimes a split or a stray letter."""
    swap = {"E": "I", "T": "M", "A": "W", "S": "H", "N": "D"}
    words = []
    for w in text.split(" "):
        r = random.random()
        if r < rough * 0.3 and len(w) > 2:
            w = w[:2] + " " + w[2:]
        elif r < rough * 0.5:
            w = re.sub(r"[A-Z]", lambda m: swap.get(m.group(0), m.group(0)), w, count=1)
        words.append(w)
    return " ".join(words) + (" E" if random.random() < rough else "")


class Demo(QObject):
    """Made-up signals sent to an Instance as feed messages, so demo runs the live code."""

    def __init__(self, inst: Instance) -> None:
        super().__init__(inst)
        self.inst = inst
        self.start = time.time()
        self.spots = 0
        self.rng = np.random.default_rng()
        self.hz_per_bin = DEMO_BAND["span_khz"] * 1000 / DEMO_BINS
        self.first_hz = DEMO_BAND["center_khz"] * 1000 - DEMO_BAND["span_khz"] * 500
        self.sigs = []
        for khz, wpm, snr, text, spot in DEMO_SIGNALS:
            k = keying(text, wpm) if text else [(1, 1.0)]
            total = sum(d for _, d in k)
            self.sigs.append({
                "khz": khz, "wpm": wpm, "snr": snr, "text": text, "spot": spot, "k": k,
                "total": total, "phase": random.random() * total,
                "next": time.time() + 2 + random.random() * 8, "seen": 0, "spotted": False,
            })
        self._send({
            "type": "hello", "v": FEED_VERSION, "call": "DEMO", "decoder": "itila2",
            "config": {
                "callsign": "DEMO", "cw_decoder": "itila2", "sample_rate": 96_000,
                "flex_daxiq_channel": None, "telnet_port": 7300, "enable_ft8": False,
                "spot_rule": "repeat",
            },
            "bands": [DEMO_BAND],
        })
        self._send({"type": "snapshot", "signals": [], "windows": [], "spots": [], "status": None})
        self.last_row = time.time()
        self.timers = []
        jobs = ((1000 // SPECTRUM_HZ, self._rows), (500, self._decode), (2000, self._status))
        for ms, fn in jobs:
            t = QTimer(self, interval=ms)
            t.timeout.connect(fn)
            t.start()
            self.timers.append(t)

    def stop(self) -> None:
        for t in self.timers:
            t.stop()

    def _send(self, m: dict[str, Any]) -> None:
        self.inst._on_text(json.dumps(m))

    @staticmethod
    def _keyed(s: dict[str, Any], t: float) -> int:
        x = (t + s["phase"]) % s["total"]
        for on, d in s["k"]:
            if x < d:
                return on
            x -= d
        return 0

    def _rows(self) -> None:
        # every row due since the last call, so a late timer still fills the waterfall
        now, step = time.time(), 1 / SPECTRUM_HZ
        t = max(self.last_row + step, now - 2)
        while t <= now:
            self._row(t)
            t += step
        self.last_row = t - step

    def _row(self, t: float) -> None:
        p = self.rng.exponential(size=(3, DEMO_BINS)).mean(axis=0)
        db = 10 * np.log10(p + 1e-3) + 1.5  # dB over the floor, as the feed sends it
        for s in self.sigs:
            sub = 1 / (SPECTRUM_HZ * DEMO_SUBSTEPS)
            on = sum(self._keyed(s, t - k * sub) for k in range(DEMO_SUBSTEPS))
            if not on:
                continue
            qsb = 1.5 * math.sin(t * 0.7 + s["phase"])  # slow QSB; partial rows give soft edges
            lvl = s["snr"] + qsb + 10 * math.log10(on / DEMO_SUBSTEPS)
            pos = (s["khz"] * 1000 - self.first_hz) / self.hz_per_bin
            b = int(pos)
            f = pos - b
            spread = (
                (b - 1, lvl - 14), (b, lvl - 6 * f), (b + 1, lvl - 6 * (1 - f)), (b + 2, lvl - 14)
            )
            for i, v in spread:
                if 0 <= i < DEMO_BINS:
                    db[i] = max(db[i], v)
        row = np.clip(db / DEMO_DB_UNIT, 0, 255).astype(np.uint8)
        head = SPECTRUM_HEADER.pack(
            FEED_VERSION, t, self.first_hz, self.hz_per_bin, DEMO_DB_UNIT, DEMO_BINS
        )
        self.inst._on_binary(head + row.tobytes())

    def _decode(self) -> None:
        now = time.time()
        for s in self.sigs:
            if not s["text"] or now < s["next"]:
                continue
            s["next"] = now + 5 + random.random() * 4
            s["seen"] += 1
            wpm = s["wpm"] + (random.random() - 0.5) * 2
            snr = s["snr"] + (random.random() - 0.5) * 3
            rough = max(0.05, 0.6 - s["snr"] / 40)
            narrow = "" if random.random() < 0.08 else misread(s["text"], rough * 1.5)
            base = {
                "type": "window", "khz": s["khz"], "wpm": round(wpm, 1), "snr": round(snr, 1),
                "utc": now,
            }
            narrow_cost = 0.5 + random.random() * 0.8 if narrow else NO_FIT
            self._send({**base, "text": narrow, "cost": narrow_cost})
            wide = misread(s["text"], rough)
            self._send({**base, "text": wide, "cost": 0.2 + random.random() * 0.6})
            if s["spot"] and not s["spotted"] and s["seen"] >= 2:
                s["spotted"] = True
                self.spots += 1
                call = next(w for w in s["text"].split() if CALL_RE.match(w))
                self._send({"type": "spot", "khz": s["khz"], "call": call, "snr": base["snr"],
                            "wpm": round(wpm), "utc": now})

    def _status(self) -> None:
        self._send({
            "type": "status", "cpu": 30 + random.randint(0, 12),
            "rss_mb": 330 + random.randint(0, 4),
            "bins": len(self.inst.signals) * 2 + 6, "spots": self.spots,
            "uptime_s": round(time.time() - self.start), "utc": time.time(),
        })


QML_IMPORT_NAME = "SparkGap"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
class Waterfall(QQuickPaintedItem):
    """Draws the selected Instance's spectrum: frequency down, time flowing left.

    New rows are coloured once, into a ring of pixel columns sized to the item; each frame only
    slides that image by the fraction of a row since the last one arrived.
    """

    viewChanged = Signal()
    instanceChanged = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._inst: Instance | None = None
        self._lo = 0.0
        self._hi = 0.0
        self._accent = QColor("#f3a640")
        self._bubble = QColor("#070b0f")
        self._sel = -1.0
        self.lut = np.zeros(256, dtype=np.uint32)
        self._canvas: QImage | None = None
        self._carr: np.ndarray | None = None
        self._cpos = 0  # next canvas column to write
        self._seen = 0  # instance rows already coloured
        self._dirty = True
        self._map: tuple[Any, ...] | None = None
        self._last_tick = 0.0
        self._gaps: list[float] = []
        self.setAntialiasing(False)
        self.setOpaquePainting(True)
        self.widthChanged.connect(self._resized)
        self.heightChanged.connect(self._resized)

    def get_instance(self) -> Instance | None:
        return self._inst

    def set_instance(self, inst: Instance | None) -> None:
        if inst is self._inst:
            return
        if self._inst:
            self._inst.changed.disconnect(self._fit)
        self._inst = inst
        if inst:
            inst.changed.connect(self._fit)
        self._fit()
        self.instanceChanged.emit()

    instance = Property(QObject, get_instance, set_instance, notify=instanceChanged)

    def _fit(self) -> None:
        inst = self._inst
        if inst and inst.n_bins and inst.go_khz:
            span = self._hi - self._lo if self._hi > self._lo else 30.0
            self.setView(inst.go_khz - span / 2, inst.go_khz + span / 2)
            inst.go_khz = None
        elif inst and inst.n_bins and not (
            inst.low_khz() <= self._lo < self._hi <= inst.high_khz()
        ):
            mid = (inst.low_khz() + inst.high_khz()) / 2
            self.setView(mid - 15, mid + 15)
        self._dirty = True
        self.update()

    def get_lo(self) -> float:
        return self._lo

    def get_hi(self) -> float:
        return self._hi

    lowKhz = Property(float, get_lo, notify=viewChanged)  # noqa: N815
    highKhz = Property(float, get_hi, notify=viewChanged)  # noqa: N815

    @Slot(float, float)
    def setView(self, lo: float, hi: float) -> None:  # noqa: N802
        inst = self._inst
        if inst and inst.n_bins:
            span = min(max(hi - lo, 2.0), inst.high_khz() - inst.low_khz())
            lo = min(max(lo, inst.low_khz()), inst.high_khz() - span)
            hi = lo + span
        if (lo, hi) == (self._lo, self._hi):
            return
        self._lo, self._hi = lo, hi
        self._dirty = True
        self.viewChanged.emit()
        self.update()

    @Slot(float, float)
    def zoom(self, factor: float, at_khz: float) -> None:
        self.setView(at_khz - (at_khz - self._lo) * factor, at_khz + (self._hi - at_khz) * factor)

    @Slot(float)
    def pan(self, dkhz: float) -> None:
        self.setView(self._lo + dkhz, self._hi + dkhz)

    @Slot(float)
    def setSelected(self, khz: float) -> None:  # noqa: N802
        self._sel = khz
        self.update()

    @Slot("QVariantList", str)
    def setColors(self, stops: list[str], accent: str) -> None:  # noqa: N802
        cols = np.array(
            [[QColor(c).red(), QColor(c).green(), QColor(c).blue()] for c in stops], float
        )
        p = np.linspace(0, len(stops) - 1, 256)
        i = np.minimum(np.floor(p).astype(int), len(stops) - 2)
        f = (p - i)[:, None]
        rgb = (cols[i] + (cols[i + 1] - cols[i]) * f).astype(np.uint32)
        self.lut = (0xFF << 24) | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]
        self._accent = QColor(accent)
        self._bubble = QColor(stops[0])
        self._dirty = True
        self.update()

    def _resized(self) -> None:
        self._dirty = True

    # ---- colouring ----

    def _build_map(self, inst: Instance, h: int) -> None:
        """Pixel row -> bins: interpolate when a bin spans pixels, max over bins otherwise."""
        bins_in_view = (self._hi - self._lo) * 1000 / inst.hz_per_bin
        px_per_bin = h / max(bins_in_view, 1e-9)
        grow = max(0, math.ceil(TRACE_MIN_PX / px_per_bin) // 2)  # widen narrow traces
        y = np.arange(h) + 0.5
        f_hz = (self._hi - y / h * (self._hi - self._lo)) * 1000  # top row = high frequency
        pos = (f_hz - inst.first_hz) / inst.hz_per_bin - 0.5
        if px_per_bin >= 1:
            i0 = np.clip(np.floor(pos).astype(int), 0, inst.n_bins - 1)
            i1 = np.clip(i0 + 1, 0, inst.n_bins - 1)
            frac = np.clip(pos - np.floor(pos), 0, 1).astype(np.float32)[:, None]
            self._map = ("interp", i0, i1, frac, grow)
        else:
            edges = (f_hz[::-1] - inst.first_hz) / inst.hz_per_bin  # ascending, bottom row first
            starts = np.clip(np.floor(edges - 0.5 / px_per_bin).astype(int), 0, inst.n_bins - 1)
            starts = np.maximum.accumulate(starts)
            end = min(inst.n_bins, int(math.ceil(edges[-1] + 0.5 / px_per_bin)) + 1)
            self._map = ("max", starts, end, grow)

    def _colour(self, inst: Instance, rows: np.ndarray) -> np.ndarray:
        """(bins, n) u8 rows -> (h, n) ARGB pixels."""
        assert self._map is not None
        v = rows.astype(np.float32) * inst.db_unit
        grow = self._map[-1]
        if grow:
            wide = v.copy()
            for k in range(1, grow + 1):
                np.maximum(wide[k:], v[:-k], out=wide[k:])
                np.maximum(wide[:-k], v[k:], out=wide[:-k])
            v = wide
        if self._map[0] == "interp":
            _, i0, i1, frac, _ = self._map
            px = v[i0] * (1 - frac) + v[i1] * frac
        else:
            _, starts, end, _ = self._map
            px = np.maximum.reduceat(v[: max(end, int(starts[-1]) + 1)], starts, axis=0)[::-1]
        idx = np.clip((px - DB_FLOOR) * (255.0 / (DB_TOP - DB_FLOOR)), 0, 255).astype(np.uint8)
        return self.lut[idx]

    def _rebuild(self, inst: Instance, w: int, h: int) -> None:
        n = w + 3
        canvas = QImage(n, h, QImage.Format.Format_RGB32)
        self._canvas = canvas
        self._carr = np.ndarray((h, n), dtype=np.uint32, buffer=canvas.bits())
        self._carr[:] = self.lut[0]
        self._cpos = 0
        self._seen = inst.total
        self._dirty = False
        if inst.ring is None:
            return
        self._build_map(inst, h)
        if not inst.cols:
            return
        k = min(inst.cols, n)
        idx = (inst.head - k + np.arange(k)) % inst.ring.shape[1]
        self._carr[:, n - k :] = self._colour(inst, inst.ring[:, idx])

    @Slot()
    def tick(self) -> None:
        if FRAME_LOG:
            now = time.perf_counter()
            if self._last_tick:
                self._gaps.append(now - self._last_tick)
                if len(self._gaps) == 1200:
                    g = np.array(self._gaps) * 1000
                    print(
                        f"frame gap ms: median {np.median(g):.1f} p90 {np.percentile(g, 90):.1f} "
                        f"p99 {np.percentile(g, 99):.1f} max {g.max():.1f}",
                        flush=True,
                    )
                    self._gaps.clear()
            self._last_tick = now
        inst = self._inst
        if not inst:
            return
        moved = inst.advance()
        w, h = int(self.width()), int(self.height())
        if w < 4 or h < 4 or inst.ring is None or self._hi <= self._lo:
            return
        if self._dirty or self._canvas is None or self._canvas.height() != h:
            self._rebuild(inst, w, h)
        elif inst.total > self._seen and self._carr is not None:
            n = self._carr.shape[1]
            k = min(inst.total - self._seen, n)
            idx = (inst.head - k + np.arange(k)) % inst.ring.shape[1]
            cols = self._colour(inst, inst.ring[:, idx])
            for c in range(k):
                self._carr[:, self._cpos] = cols[:, c]
                self._cpos = (self._cpos + 1) % n
            self._seen = inst.total
        if moved:
            self.update()

    def paint(self, p: QPainter) -> None:
        w, h = int(self.width()), int(self.height())
        p.fillRect(0, 0, w, h, QColor(int(self.lut[0])))
        inst = self._inst
        if not inst or self._canvas is None or self._hi <= self._lo:
            return
        n = self._canvas.width()
        # oldest column at _cpos; newest column's right edge slides from w + 1 to w
        right = w + (1.0 - inst.frac)
        x0 = right - n
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        a = n - self._cpos
        p.drawImage(QRectF(x0, 0, a, h), self._canvas, QRectF(self._cpos, 0, a, h))
        if self._cpos:
            p.drawImage(QRectF(x0 + a, 0, self._cpos, h), self._canvas, QRectF(0, 0, self._cpos, h))
        if self._sel > 0:
            y = (self._hi - self._sel) / (self._hi - self._lo) * h
            p.setPen(QPen(self._accent, 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(0, y), QPointF(w, y))


def band_label(b: dict[str, Any]) -> str:
    """A band's name; a band given as a frequency is named by the amateur band it falls in."""
    if not str(b["name"]).isdigit():
        return str(b["name"])
    khz = float(b["center_khz"])
    return next((n for n, lo, hi in HAM_BANDS if lo <= khz <= hi), f"{khz:g} kHz")


def refusal(err: str) -> str:
    """"code" or "lockout" when a socket error is the feed refusing the join code, else ""."""
    # Qt reports the feed's 401 as an unsupported WWW-Authenticate challenge
    if "WWW-Authenticate" in err or "401" in err:
        return "code"
    return "lockout" if "429" in err else ""


def open_request(url: str, token: str) -> QNetworkRequest:
    u = QUrl(url)
    if u.host().lower() == "localhost":  # may resolve to ::1 first; the feed listens on IPv4
        u.setHost("127.0.0.1")
    req = QNetworkRequest(u)
    req.setRawHeader(b"Sec-WebSocket-Protocol", PROTOCOL.encode())
    if token:
        req.setRawHeader(b"Authorization", f"Bearer {token}".encode())
    return req


class Probe(QObject):
    """One try of a feed: done(port, result), result "ok" once its hello arrives, else "code",
    "lockout", "full" or "none"."""

    done = Signal(int, str)

    def __init__(self, url: str, token: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.port = url_parts(url)[1]
        self.result = ""
        self.ws = QWebSocket()
        self.ws.textMessageReceived.connect(self._on_text)
        self.ws.errorOccurred.connect(self._on_error)
        self.ws.disconnected.connect(lambda: self._finish("none"))
        QTimer.singleShot(int(SCAN_TIMEOUT_S * 1000), self, lambda: self._finish("none"))
        self.ws.open(open_request(url, token))

    def _on_text(self, text: str) -> None:
        try:
            if json.loads(text).get("type") == "hello":
                self._finish("ok")
        except (ValueError, AttributeError):
            pass

    def _on_error(self, _err: Any) -> None:
        err = self.ws.errorString()
        self._finish(refusal(err) or ("full" if "503" in err else "none"))

    def _finish(self, result: str) -> None:
        if self.result:
            return
        self.result = result
        self.ws.abort()
        self.done.emit(self.port, result)


def scan_report(found: dict[int, str], listed: list[int],
                stale: list[int] | None = None) -> tuple[list[int], list[int], str]:
    """The ports a scan adds, the listed ones whose code it updates (stale: saved with another
    code), and a line for the operator."""
    def ports(r: str) -> list[int]:
        return sorted(p for p, v in found.items() if v == r)

    stale = stale or []
    add = [p for p in ports("ok") if p not in listed]
    update = [p for p in ports("ok") if p in stale]
    have = [p for p in ports("ok") if p in listed and p not in stale]
    def joined(ps: list[int]) -> str:
        return ", ".join(map(str, ps))

    parts = []
    if add:
        parts.append(f"Added {joined(add)}.")
    if update:
        parts.append(f"Join code updated on {joined(update)}.")
    if have:
        parts.append(f"Already in the list: {joined(have)}.")
    if ports("code"):
        parts.append(f"Different join code on {joined(ports('code'))}.")
    if ports("lockout"):
        parts.append(f"Locked out for {LOCKOUT_S // 60} min on {joined(ports('lockout'))}.")
    if ports("full"):
        parts.append(f"Too many clients on {joined(ports('full'))}.")
    if not parts:
        parts.append(f"No SparkGap answered on ports {SCAN_PORTS[0]} to {SCAN_PORTS[-1]}.")
    return add, update, " ".join(parts)


def feed_url(address: str, port: int) -> str:
    return f"ws://{address.strip()}:{port}/feed"


def url_parts(url: str) -> tuple[str, int]:
    """(address, port) of a feed URL; the port defaults when absent."""
    q = QUrl(url)
    return q.host(), q.port(DEFAULT_PORT)


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch not in " -")


def check_feed(address: str, port: int, code: str = "") -> str:
    """Why a server entry cannot be used, or "" when it can. An empty code suits a local server."""
    a = address.strip()
    if not a or any(ch.isspace() or ch in "/:#?@" for ch in a):
        return "address: a VPN or local IP address, nothing else"
    if not 0 < port < 65_536:
        return "port: 1 to 65535"
    c = normalize_code(code)
    if c and (len(c) != CODE_LENGTH or any(ch not in CODE_ALPHABET for ch in c)):
        return f"join code: {CODE_LENGTH} letters and digits, as SparkGap's log shows it"
    return ""


def load_feeds(store: QSettings) -> list[tuple[str, str]]:
    n = store.beginReadArray("connections")
    feeds = []
    for i in range(n):
        store.setArrayIndex(i)
        feeds.append((str(store.value("url", "")), str(store.value("token", ""))))
    store.endArray()
    return [f for f in feeds if f[0]]


def save_feeds(store: QSettings, feeds: list[tuple[str, str]]) -> None:
    store.remove("connections")
    store.beginWriteArray("connections", len(feeds))
    for i, (url, token) in enumerate(feeds):
        store.setArrayIndex(i)
        store.setValue("url", url)
        store.setValue("token", token)
    store.endArray()
    store.sync()


Feeds = list[tuple[str, str]]  # (url, token) per server


def merge_feeds(saved: Feeds, extra: Feeds) -> Feeds:
    """Saved servers plus those from the command line; a URL already saved takes the newer token."""
    out = dict(saved)
    out.update(extra)
    return list(out.items())


class Console(QObject):
    """Root object: the list of instances and the current one."""

    instancesChanged = Signal()
    currentChanged = Signal()
    scanDone = Signal(bool, str)  # whether servers were added or updated, and what the search found

    def __init__(self, feeds: list[tuple[str, str]], store: QSettings | None = None) -> None:
        super().__init__()
        self._store = store  # where the server list is kept; None keeps it in memory only
        self._instances = [Instance(u, t, self) for u, t in feeds]
        self._current = 0
        self._back = 0  # the server shown before demo
        self._scan: dict[int, str] = {}  # port -> result of the search under way
        self._probes: list[Probe] = []
        self._scan_at = ("", "")  # (address, code) being searched
        self._scan_edit = -1  # the row the search replaces, or -1 when adding
        for i in self._instances:
            i.open()
        # instances not on screen still move their spectrum rows into history, so switching to
        # one shows its recent waterfall (the shown one is advanced by the waterfall each frame)
        self._bg = QTimer(self, interval=50)
        self._bg.timeout.connect(self._advance_background)
        self._bg.start()

    @Property(bool, notify=instancesChanged)
    def demo(self) -> bool:
        return any(i.is_demo for i in self._instances)

    @Slot(bool)
    def setDemo(self, on: bool) -> None:  # noqa: N802
        """Demo is a view: the servers stay connected and keep their history while it shows."""
        if self._store is not None:
            self._store.setValue("ui/demo", on)
        at = next((n for n, i in enumerate(self._instances) if i.is_demo), -1)
        if on and at < 0:
            self._back = self._current
            inst = Instance(DEMO_URL, "", self)
            self._instances.append(inst)
            inst.open()
            self._current = len(self._instances) - 1
        elif not on and at >= 0:
            old = self._instances.pop(at)
            old.close()
            old.deleteLater()
            self._current = min(self._back, max(0, len(self._instances) - 1))
        else:
            return
        self.instancesChanged.emit()
        self.currentChanged.emit()

    def _advance_background(self) -> None:
        for n, inst in enumerate(self._instances):
            if n != self._current:
                inst.advance()

    @Property(list, notify=instancesChanged)
    def instances(self) -> list[Instance]:
        return list(self._instances)

    @Property(QObject, notify=currentChanged)
    def current(self) -> Instance | None:
        return self._instances[self._current] if self._instances else None

    @Property(int, notify=currentChanged)
    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    @Slot(int)
    def setCurrent(self, i: int) -> None:  # noqa: N802
        if 0 <= i < len(self._instances) and i != self._current:
            self._current = i
            self.currentChanged.emit()


    # ---- server list, edited in Settings ----

    def _save(self) -> None:
        if self._store is not None:
            save_feeds(self._store, [i.feed for i in self._instances if not i.is_demo])

    @Slot(int, result="QVariantMap")
    def feedAt(self, i: int) -> dict[str, Any]:  # noqa: N802
        if not 0 <= i < len(self._instances):
            return {}
        url, token = self._instances[i].feed
        address, port = url_parts(url)
        return {"address": address, "port": port, "token": token}

    @Slot(int, str, int, str, result=str)
    def saveFeed(self, i: int, address: str, port: int, token: str) -> str:  # noqa: N802
        """Adds a server (i < 0) or replaces server i. Returns why it was refused, or ""."""
        if err := check_feed(address, port, token):
            return err
        url = feed_url(address, port)
        if any(n != i and inst.feed[0] == url for n, inst in enumerate(self._instances)):
            return "already in the list"
        inst = Instance(url, normalize_code(token), self)
        if 0 <= i < len(self._instances):
            old = self._instances[i]
            self._instances[i] = inst
            old.close()
            old.deleteLater()
        else:
            self._instances.append(inst)
            i = len(self._instances) - 1
        inst.open()
        self._current = i
        self._save()
        self.instancesChanged.emit()
        self.currentChanged.emit()
        return ""

    @Slot(int, str, str, result=str)
    def findFeeds(self, i: int, address: str, token: str) -> str:  # noqa: N802
        """Tries every port in SCAN_PORTS on address with one code; scanDone reports. i: the row
        being edited, which the result replaces, or -1. Returns why it cannot start, or ""."""
        if err := check_feed(address, DEFAULT_PORT, token):
            return err
        if self._scan_at[0]:
            return "already searching"
        address, token = address.strip(), normalize_code(token)
        self._scan, self._scan_at = {}, (address, token)
        self._scan_edit = i if 0 <= i < len(self._instances) else -1
        self._probes = [Probe(feed_url(address, port), token, self) for port in SCAN_PORTS]
        for probe in self._probes:
            probe.done.connect(self._probed)
        return ""

    def _probed(self, port: int, result: str) -> None:
        self._scan[port] = result
        if len(self._scan) < len(SCAN_PORTS):
            return
        for probe in self._probes:
            probe.deleteLater()
        self._probes = []
        address, token = self._scan_at
        edit, self._scan_at, self._scan_edit = self._scan_edit, ("", ""), -1
        others = [inst for n, inst in enumerate(self._instances) if n != edit
                  and not inst.is_demo and url_parts(inst.feed[0])[0] == address]
        listed = [url_parts(inst.feed[0])[1] for inst in others]
        stale = [url_parts(inst.feed[0])[1] for inst in others if inst.feed[1] != token]
        add, update, text = scan_report(self._scan, listed, stale)
        if add or update:
            at = len(self._instances)
            if 0 <= edit < len(self._instances):
                self._drop(edit)
                at = edit
            for port in update:
                k = next(n for n, inst in enumerate(self._instances)
                         if inst.feed[0] == feed_url(address, port))
                self._drop(k)
                self._insert(k, Instance(feed_url(address, port), token, self))
            for k, port in enumerate(add):
                self._insert(min(at + k, len(self._instances)),
                             Instance(feed_url(address, port), token, self))
            self._current = (min(at, len(self._instances) - 1) if add else next(
                n for n, inst in enumerate(self._instances)
                if inst.feed[0] == feed_url(address, update[0])))
            self._save()
            self.instancesChanged.emit()
            self.currentChanged.emit()
        self.scanDone.emit(bool(add or update), text)

    def _drop(self, i: int) -> None:
        old = self._instances.pop(i)
        old.close()
        old.deleteLater()

    def _insert(self, i: int, inst: Instance) -> None:
        self._instances.insert(i, inst)
        inst.open()

    @Slot(int)
    def removeFeed(self, i: int) -> None:  # noqa: N802
        if not 0 <= i < len(self._instances):
            return
        old = self._instances.pop(i)
        old.close()
        old.deleteLater()
        if self._current >= len(self._instances) or self._current > i:
            self._current = max(0, self._current - 1)
        self._save()
        self.instancesChanged.emit()
        self.currentChanged.emit()
