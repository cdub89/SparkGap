"""The monitor's decoder options (itila2_set_monitor, cdub89/SparkGap#63): each over timed alone,
and the calls and prosigns heard in the QSO earn a token prior.  The windows never set them."""
import ctypes as ct
from pathlib import Path

import numpy as np
import pytest

_LIB = Path(__file__).resolve().parent.parent / "libitila2.so"
if not _LIB.exists():
    pytest.skip("libitila2.so not built", allow_module_level=True)
lib = ct.CDLL(str(_LIB))
if not hasattr(lib, "itila2_set_monitor"):
    pytest.skip("libitila2.so has no itila2_set_monitor", allow_module_level=True)
P = ct.POINTER(ct.c_double)
lib.itila_create.restype = ct.c_void_p
lib.itila_create.argtypes = [ct.c_int, ct.c_double]
lib.itila_free.argtypes = [ct.c_void_p]
lib.itila2_set_monitor.restype = ct.c_int
lib.itila2_set_monitor.argtypes = [ct.c_void_p, ct.c_int, ct.c_char_p, ct.c_double]
lib.itila2_stream_step.restype = ct.c_int
lib.itila2_stream_step.argtypes = [ct.c_void_p, P, ct.c_int, ct.c_int, ct.c_double, ct.c_char_p,
                                   ct.c_char_p, ct.c_int, ct.POINTER(ct.c_int), P, ct.c_int]

MORSE = {"C": "-.-.", "Q": "--.-", "D": "-..", "E": ".", "W": ".--", "1": ".----", "A": ".-",
         "O": "---", "N": "-.", "T": "-", "S": "...", "R": ".-.", "M": "--", "G": "--.",
         "K": "-.-", "H": "....", "I": "..", "V": "...-", "U": "..-", "7": "--...", "Z": "--.."}
RATE = 200


def _keyed(text: str, unit: int, stretch: dict[tuple[str, int], float] | None = None) -> list[int]:
    out: list[int] = []
    for wi, w in enumerate(text.split()):
        if wi:
            out += [0] * 7 * unit
        for ci, ch in enumerate(w):
            if ci:
                out += [0] * int((stretch or {}).get((w, ci), 3) * unit)
            for ei, el in enumerate(MORSE[ch]):
                if ei:
                    out += [0] * unit
                out += [1] * (unit if el == "." else 3 * unit)
    return out


def _env(x: list[int]) -> np.ndarray:
    a = np.array([0] * 100 + x + [0] * 300, float)
    return np.ascontiguousarray(0.05 + a + 0.08 * np.random.default_rng(0).standard_normal(len(a)))


def _decode(env: np.ndarray, start: int, per_over: int, words: bytes = b"") -> str:
    h = lib.itila_create(RATE, 200.0)
    try:
        assert lib.itila2_set_monitor(h, per_over, words, 2.0) == 0
        stable, live = ct.create_string_buffer(1024), ct.create_string_buffer(1024)
        last, ev = ct.c_int(), ct.c_double()
        lib.itila2_stream_step(h, env.ctypes.data_as(P), len(env), start, 1.5, stable, live, 1024,
                               ct.byref(last), ct.byref(ev), len(env) - 1)
        return stable.value.decode()
    finally:
        lib.itila_free(h)


def test_each_over_is_timed_on_its_own() -> None:
    """A slow over, 2 s quiet, then a fast one: the window's fits are the slow fist's."""
    slow = _keyed("CQ CQ DE W1AW W1AW TEST DE W1AW K", 16)
    fast = _keyed("GOOD MORNING TOM SO GOOD TO HEAR", 8)
    env = _env(slow + [0] * 400 + fast)
    start = 100 + len(slow) + 390
    assert "GOOD MORNING" not in _decode(env, start, per_over=0)
    assert _decode(env, start, per_over=1).startswith("GOOD MORNING TOM SO GOOD TO HEAR")


def test_one_over_decodes_as_before() -> None:
    env = _env(_keyed("GOOD MORNING TOM SO GOOD TO HEAR", 10))
    assert _decode(env, 0, per_over=1) == _decode(env, 0, per_over=0)


def test_a_call_heard_in_the_qso_is_kept_whole() -> None:
    """A borderline gap inside a call (4.5 units): the heard call decides, as a known call does."""
    env = _env(_keyed("DE KM7AZ KM7AZ K", 12, {("KM7AZ", 4): 4.5}))
    assert "KM7A Z" in _decode(env, 0, per_over=0)
    assert "KM7AZ KM7AZ" in _decode(env, 0, per_over=0, words=b"KM7AZ\nBK")
