"""flex_rxant / flex_loopa / flex_loopb reach our pan and slice, and nothing is sent without them."""

import pytest

import flex_iq
from flex_iq import FlexIQReceiver

Monkey = pytest.MonkeyPatch
HANDLE = "7F19D8AE"
DEFAULT_PAN = [f"S{HANDLE}|display pan 0x40000001 client_handle=0x{HANDLE} center=14.100000 "
               "rxant=ANT1 loopa=0 loopb=0 daxiq_channel=0",
               f"S{HANDLE}|slice 0 in_use=1 RF_frequency=14.100000 client_handle=0x{HANDLE} pan=0x40000001"]
STATION = "0x79D60253"    # another copy holds the station's GUI client; we bind to it
OWNER = f"S0|client {STATION} connected client_id=AAAA-1 program=SparkGap station=SparkGap-Shack"
SAVED_PAN = [f"S0|display pan 0x40000002 client_handle={STATION} center=7.038400",
             "S0|display pan 0x40000002 daxiq_channel=3 ",
             f"S0|slice 2 in_use=1 RF_frequency=7.038400 client_handle={STATION} pan=0x40000002"]

class _Sock:
    def settimeout(self, *_: object) -> None:
        pass

    def connect(self, *_: object) -> None:
        pass

    def setsockopt(self, *_: object) -> None:
        pass

    def bind(self, *_: object) -> None:
        pass


def _setup(monkeypatch: Monkey, bound: bool = False, refused: str = "", **kw: object) -> list[str]:
    """Run _setup against a radio that gives us its default pan and slice, or, bound, our station's
    pan saved on our channel; a command starting with `refused` is refused.  Returns the commands."""
    rx = FlexIQReceiver("192.0.2.1", freq_hz=14_246_000, sample_rate=192_000, daxiq_channel=3, **kw)
    sent: list[str] = []

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        rx._seq += 1
        sent.append(c)
        if refused and c.startswith(refused):
            return [f"R{rx._seq}|50000016|"]
        if c == "info":
            return [f'R{rx._seq}|0|model="FLEX-6700",name="Shack"']
        if c == "sub client all" and bound:
            return [OWNER, f"R{rx._seq}|0|"]
        if c in ("sub pan all", "sub slice all"):
            return (SAVED_PAN if bound else DEFAULT_PAN) + [f"R{rx._seq}|0|"]
        if c.startswith("stream create"):
            return [f"R{rx._seq}|0|0x20000004"]
        return [f"R{rx._seq}|0|"]

    monkeypatch.setattr(flex_iq.socket, "socket", lambda *_: _Sock())
    monkeypatch.setattr(rx, "_drain", lambda *_: ["V1.4.0.0", f"H{HANDLE}"])
    monkeypatch.setattr(rx, "_cmd", cmd)
    if refused:
        with pytest.raises(RuntimeError, match="refused"):
            rx._setup()
        assert rx.state != "live" and not rx._pan_bound
        return sent
    rx._setup()
    assert rx.state == "live"
    assert (rx._pan_id, rx._slice_id) == ((0x4000_0002, 2) if bound else (0x4000_0001, 0))
    return sent

def test_loop_settings_go_with_the_pan_tuning_and_onto_the_slice(monkeypatch: Monkey) -> None:
    sent = _setup(monkeypatch, rxant="ANT1", loopb=True)
    assert "display pan set 0x40000001 center=14.246000 bandwidth=0.192000 rxant=ANT1 loopb=1" in sent
    assert "slice set 0 rxant=ANT1 loopb=1" in sent
    # the loop is set in the same command that tunes the pan, before the slice is touched
    assert sent.index("slice set 0 rxant=ANT1 loopb=1") > sent.index("slice tune 0 14.246000")


def test_loop_off_is_sent_as_zero(monkeypatch: Monkey) -> None:
    sent = _setup(monkeypatch, loopa=False, loopb=True)
    assert "slice set 0 loopa=0 loopb=1" in sent


def test_without_options_the_radio_keeps_its_band_settings(monkeypatch: Monkey) -> None:
    sent = _setup(monkeypatch)
    assert "display pan set 0x40000001 center=14.246000 bandwidth=0.192000" in sent
    assert not any("loop" in c or "rxant" in c for c in sent)


def test_a_bound_copy_sets_the_loop_on_its_stations_saved_pan_and_slice(monkeypatch: Monkey) -> None:
    sent = _setup(monkeypatch, bound=True, loopb=True)
    assert "client bind client_id=AAAA-1" in sent
    assert "display pan set 0x40000002 center=14.246000 bandwidth=0.192000 loopb=1" in sent
    assert "slice set 2 loopb=1" in sent


def test_a_loop_the_slice_refuses_fails_the_start(monkeypatch: Monkey) -> None:
    # a radio without the loop takes the pan command but not the slice's
    sent = _setup(monkeypatch, refused="slice set 0 loopa=", loopa=True)
    assert not any(c.startswith("stream create") for c in sent)


def test_a_loop_the_pan_refuses_fails_the_start(monkeypatch: Monkey) -> None:
    sent = _setup(monkeypatch, refused="display pan set 0x40000001 center=", loopa=True)
    assert not any(c.startswith("slice tune") or c.startswith("stream create") for c in sent)
