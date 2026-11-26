"""Click to decode (exp/click-decode): one monitor at an exact frequency, decoded in near real time.

The scanner's monitor bin (itila_sc_monitor_open) makes the envelope with the bins' own DSP
(AFC, tone lock); every STEP_S of audio this decodes the newest WINDOW_S of it with
itila2_stream_step, so the fits are a 30 s window's, and commits the words the window cut has
passed since the last step.  Display only: spots, bins and windows never see it.
"""

import ctypes as ct
from dataclasses import dataclass

import numpy as np

RATE_HZ = 200          # envelope samples per second
WINDOW_S = 30.0        # decoded each step, as a batch window
STEP_S = 0.5           # audio between steps
WIDE_LPF_HZ = 200.0    # the wide path, as the scanner's h200
TEXT_LEN = 1024

P = ct.POINTER(ct.c_double)


@dataclass
class Word:
    end_utc: float      # audio time of the text's last key-down
    commit_utc: float   # audio time of the step that committed it
    wpm: float | None
    text: str


def _bind(sc_lib: ct.CDLL, dec_lib: ct.CDLL) -> bool:
    if not hasattr(sc_lib, 'itila_sc_monitor_open') or not hasattr(dec_lib, 'itila2_stream_step'):
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
    dec_lib.itila2_stream_step.restype = ct.c_int
    dec_lib.itila2_stream_step.argtypes = [ct.c_void_p, P, ct.c_int, ct.c_int, ct.c_double,
                                           ct.c_char_p, ct.c_char_p, ct.c_int, ct.POINTER(ct.c_int)]
    return True


class Monitor:
    """One monitor on one scanner.  feed(now) after each IQ block; words collects the commits."""

    def __init__(self, sc_lib: ct.CDLL, sc_handle: int, dec_lib: ct.CDLL, ev_thresh: float) -> None:
        if not _bind(sc_lib, dec_lib):
            raise RuntimeError("no monitor in these libraries (itila2, exp/click-decode)")
        self._sc_lib, self._sc, self._dec_lib = sc_lib, sc_handle, dec_lib
        self._ev = ev_thresh
        self._h: int | None = None
        self._env = np.zeros(int(WINDOW_S * RATE_HZ))
        self._stable = ct.create_string_buffer(TEXT_LEN)
        self._live = ct.create_string_buffer(TEXT_LEN)
        self._last = ct.c_int(-1)
        self.khz: float | None = None
        self.live = ""
        self.words: list[Word] = []
        self._commit = 0            # samples since the monitor opened, up to the last cut
        self._next_step = 0.0

    def open(self, khz: float) -> bool:
        self.close()
        if not self._sc_lib.itila_sc_monitor_open(self._sc, khz * 1000.0):
            return False
        self._h = self._dec_lib.itila_create(RATE_HZ, WIDE_LPF_HZ)
        if not self._h:
            self._sc_lib.itila_sc_monitor_close(self._sc)
            return False
        self.khz, self.live, self._commit, self._next_step = khz, "", 0, 0.0
        return True

    def close(self) -> None:
        if self.khz is not None:
            self._sc_lib.itila_sc_monitor_close(self._sc)
        if self._h:
            self._dec_lib.itila_free(self._h)
        self._h, self.khz = None, None

    def feed(self, now: float) -> list[Word]:
        """A step when STEP_S of audio has passed; returns the words it committed."""
        if self._h is None or now < self._next_step:
            return []
        self._next_step = now + STEP_S
        end, afc = ct.c_longlong(0), ct.c_double(0.0)
        n = self._sc_lib.itila_sc_monitor_read(self._sc, None, self._env.ctypes.data_as(P),
                                               len(self._env), ct.byref(end), ct.byref(afc))
        if n < RATE_HZ:
            return []
        start = end.value - n
        cut = self._dec_lib.itila2_stream_step(
            self._h, self._env.ctypes.data_as(P), n, max(0, self._commit - start), self._ev,
            self._stable, self._live, TEXT_LEN, ct.byref(self._last))
        if cut < 0:
            return []
        self._commit = start + cut
        self.live = self._live.value.decode(errors='replace').strip()
        text = self._stable.value.decode(errors='replace').strip()
        if not text:
            return []
        wpm = self._dec_lib.itila_get_wpm(self._h)
        last = self._last.value if self._last.value >= 0 else cut
        word = Word(now - (n - last) / RATE_HZ, now, wpm if wpm > 0 else None, text)
        self.words.append(word)
        return [word]

