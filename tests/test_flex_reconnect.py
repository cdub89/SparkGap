"""flex_iq reconnects without leaving objects on the radio, and stop() wins over a reconnect."""

import logging
import struct

import pytest

import flex_iq
from flex_iq import FlexIQReceiver

Monkey = pytest.MonkeyPatch
STREAM = 0x2000_0000


class _Tcp:
    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.sent: list[bytes] = []

    def recv(self, *_: object) -> bytes:
        return self.data

    def sendall(self, b: bytes) -> None:
        self.sent.append(b)

    def close(self) -> None:
        pass


class _Udp:
    def recvfrom(self, *_: object) -> tuple[bytes, tuple[str, int]]:
        head = struct.pack(">BBHI", 0x38, 0x01, 0, STREAM) + bytes(flex_iq.VITA_HEADER_SIZE - 8)
        return head + bytes(8 * 4), ("192.0.2.1", 4991)

    def close(self) -> None:
        pass


def _live(monkeypatch: Monkey) -> tuple[FlexIQReceiver, list[str]]:
    """A receiver streaming on pan 0x40000001, slice 2, stream STREAM; records events in order."""
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp, rx._udp, rx._handle = _Tcp(), _Udp(), "7F19D8AE"  # pyright: ignore[reportAttributeAccessIssue]
    rx._stream_id, rx._slice_id, rx._pan_id, rx._running = STREAM, 2, 0x4000_0001, True
    rx._pan_bound, rx._stream_handle = True, 0x7F19_D8AE
    events: list[str] = []

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        events.append(c)
        return OURS.get(c, []) + [f"R{rx._seq}|0|"]

    def close(reason: str) -> None:
        events.append(f"close {reason}")
        rx._tcp = rx._udp = None

    monkeypatch.setattr(rx, "_cmd", cmd)
    monkeypatch.setattr(rx, "_close", close)
    monkeypatch.setattr(rx, "start", lambda why=None: events.append("start"))
    monkeypatch.setattr(flex_iq.os, "kill", lambda _pid, _sig: events.append("kill"))
    return rx, events


def _stream(pan: int, client: int = 0x7F19_D8AE, sid: int = STREAM) -> str:
    """A stream status line as the radio sends it (Y.log, 2026-10-20)."""
    return (f"S{client:08X}|stream 0x{sid:08X} type=dax_iq daxiq_channel=1 pan=0x{pan:08X} slice=0x0 "
            f"endpoint_type=Display daxiq_rate=96000 client_handle=0x{client:08X} active=1")


OURS = {"sub daxiq all": [_stream(0x4000_0001)],
        "sub pan all": ["S0|display pan 0x40000001 client_handle=0x7F19D8AE center=14.038400",
                        "S0|display pan 0x40000001 daxiq_channel=1 "],
        "sub slice all": ["S0|slice 2 in_use=1 pan=0x40000001 client_handle=0x7F19D8AE"]}
CHECK = ["sub daxiq all", "sub pan all", "sub slice all"]
REMOVE = CHECK + ["stream remove 0x20000000", "slice remove 2", "display pan remove 0x40000001"]


def test_a_stall_removes_our_objects_before_reconnecting(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)
    rx._restart("No DAX-IQ packets for 10 s", True)
    assert events == REMOVE + ["close reconnecting", "start"]


def test_a_lost_connection_sends_nothing_into_the_dead_socket(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)
    rx._restart("The radio closed the control connection", False)
    assert events == ["close reconnecting", "start"] and rx._stale is None


def test_a_bound_copy_remembers_what_its_station_keeps(monkeypatch: Monkey) -> None:
    rx, _ = _live(monkeypatch)
    rx._bound = True
    rx._restart("The radio closed the control connection", False)
    assert rx._stale == (STREAM, 2, 0x4000_0001, 0x7F19_D8AE)


def test_a_second_lost_connection_halts_without_commands(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)
    rx._last_loss = flex_iq.time.time() - 31
    rx._restart("The radio closed the control connection", False)
    assert events == ["close halted", "kill"] and rx.state == "halted"


def test_no_reconnect_once_stopping(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)
    rx._stopping = True
    rx._restart("No DAX-IQ packets for 10 s", True)
    assert events == [] and rx.reconnects == 0


def test_stop_removes_our_objects(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)
    monkeypatch.setattr(flex_iq.time, "sleep", lambda _s: None)
    rx.stop()
    assert events == REMOVE + ["close stopping"] and rx._stopping and not rx._running


LAKE_GUI = "S0|client 0x79D60253 connected client_id=AAAA-1 program=SparkGap station=SparkGap-Lake"


class _Stop(Exception):
    pass


class _Sock:
    def settimeout(self, *_: object) -> None:
        pass

    def connect(self, *_: object) -> None:
        pass


def test_a_bound_copy_removes_what_it_left_after_reattaching(monkeypatch: Monkey) -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000, daxiq_channel=3)
    rx._stale = (STREAM, 2, 0x4000_0002, 0x5F91_167C)   # made by our previous connection
    sent: list[str] = []
    left = {"sub daxiq all": [_stream(0x4000_0002, 0x5F91_167C)],
            "sub pan all": ["S0|display pan 0x40000002 client_handle=0x79D60253",
                            "S0|display pan 0x40000002 daxiq_channel=3 "],
            "sub slice all": ["S0|slice 2 in_use=1 pan=0x40000002 client_handle=0x79D60253"]}

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        if c == "sub daxiq all" and "sub daxiq all" in sent:
            raise _Stop
        rx._seq += 1
        sent.append(c)
        if c == "info":
            return [f'R{rx._seq}|0|model="FLEX-6600",name="Lake"']
        status = [LAKE_GUI] if c == "sub client all" else left.get(c, [])
        return status + [f"R{rx._seq}|0|"]

    monkeypatch.setattr(flex_iq.socket, "socket", lambda *_: _Sock())
    monkeypatch.setattr(rx, "_drain", lambda *_: ["V1.4.0.0", "H7F19D8AE"])
    monkeypatch.setattr(rx, "_cmd", cmd)
    with pytest.raises(_Stop):
        rx._setup()
    at = sent.index("client udpport 7791")
    assert sent[at + 1:at + 7] == CHECK + ["stream remove 0x20000000", "slice remove 2",
                                           "display pan remove 0x40000002"]
    assert rx._stale is None and rx._pan_id is None


def _receive_until_restart(monkeypatch: Monkey, ready: list[str]) -> tuple[str, bool]:
    """Drive receive() with select returning `ready` in turn ("tcp", "udp" or "").
    Returns the arguments _restart was called with."""
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp, rx._udp = _Tcp(), _Udp()  # pyright: ignore[reportAttributeAccessIssue]
    rx._stream_id, rx._running = STREAM, True
    turns = iter(ready)

    def select(*_: object) -> tuple[list[object], list[object], list[object]]:
        name = next(turns)
        return ([getattr(rx, f"_{name}")] if name else []), [], []

    called: list[tuple[str, bool]] = []

    def restart(why: str, alive: bool) -> None:
        called.append((why, alive))
        rx._running = False

    monkeypatch.setattr(flex_iq.select, "select", select)
    monkeypatch.setattr(rx, "_restart", restart)
    rx.receive(lambda *_: None)
    assert len(called) == 1
    return called[0]


def test_receive_reconnects_a_closed_control_connection(monkeypatch: Monkey) -> None:
    lost = ("The radio closed the control connection", False)
    assert _receive_until_restart(monkeypatch, ["tcp"]) == lost


def test_receive_reconnects_a_stalled_stream_on_a_live_connection(monkeypatch: Monkey) -> None:
    monkeypatch.setattr(flex_iq, "STALL_S", -1)
    why, alive = _receive_until_restart(monkeypatch, ["udp", ""])
    assert why.startswith("No DAX-IQ packets for -1 s") and alive


PTT = "S0|client 0x7F19D8AE connected local_ptt=1 program=SparkGap station=SparkGap-Lake"


def test_ptt_warning_once_per_connection(caplog: pytest.LogCaptureFixture) -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp = _Tcp(f"{PTT}\n".encode())  # pyright: ignore[reportAttributeAccessIssue]
    with caplog.at_level(logging.WARNING, logger="sparkgap.flex"):
        rx._read_status()
        rx._read_status()
    assert sum("local PTT" in r.getMessage() for r in caplog.records) == 1


def test_no_disconnect_line_when_never_connected(caplog: pytest.LogCaptureFixture) -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp = _Tcp()  # pyright: ignore[reportAttributeAccessIssue]
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._close("failed start")
    assert not caplog.records and rx._tcp is None


def _status(rx: FlexIQReceiver, events: list[str], monkeypatch: Monkey, now: dict[str, list[str]]) -> None:
    monkeypatch.setattr(rx, "_cmd", lambda c, timeout=2.0, settle=0.0:
                        events.append(c) or now.get(c, []) + [f"R{rx._seq}|0|"])


def test_after_the_owner_leaves_our_stream_goes_and_the_pan_stays(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)    # Y, 2026-10-20 11:21Z: the owner left, the radio dropped our pan
    _status(rx, events, monkeypatch, {"sub daxiq all": [_stream(0)]})
    rx._restart("No DAX-IQ packets for 10 s", True)
    assert events == CHECK + ["stream remove 0x20000000", "close reconnecting", "start"]


def test_a_stream_id_another_client_now_has_is_left_alone(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)    # the radio reuses ids: O2 got 0x2000000B after O1 removed it
    _status(rx, events, monkeypatch, {**OURS, "sub daxiq all": [_stream(0x4000_0001, 0x303A_65A4)]})
    monkeypatch.setattr(flex_iq.time, "sleep", lambda _s: None)
    rx.stop()
    assert events == CHECK + ["close stopping"]


def test_a_failed_start_removes_the_pan_it_made_before_the_bind(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)    # Qch5: the new pan came with a saved channel 2, still ours
    rx._stream_id, rx._pan_bound = None, False
    _status(rx, events, monkeypatch, {"sub pan all": ["S0|display pan 0x40000001 client_handle=0x7F19D8AE",
                                                      "S0|display pan 0x40000001 daxiq_channel=2 "],
                                      "sub slice all": OURS["sub slice all"]})
    rx._remove_objects()
    assert events == CHECK + ["slice remove 2", "display pan remove 0x40000001"]


def test_a_pan_another_stream_feeds_is_left_alone(monkeypatch: Monkey) -> None:
    rx, events = _live(monkeypatch)
    rx._stream_id, rx._pan_bound = None, False
    _status(rx, events, monkeypatch, {**OURS, "sub daxiq all": [_stream(0x4000_0001, 0x5F91_167C, 0x2000_0011)]})
    rx._remove_objects()
    assert events == CHECK


def test_a_copy_back_as_owner_still_removes_what_it_left(monkeypatch: Monkey) -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000, daxiq_channel=3)
    rx._stale = (STREAM, None, None, 0x5F91_167C)   # was bound; the station's owner has gone since
    sent: list[str] = []

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        if c == "sub client all" and "sub daxiq all" in sent:
            raise _Stop
        rx._seq += 1
        sent.append(c)
        if c == "info":
            return [f'R{rx._seq}|0|model="FLEX-6600",name="Lake"']
        status = [_stream(0, 0x5F91_167C)] if c == "sub daxiq all" else []
        return status + [f"R{rx._seq}|0|"]

    monkeypatch.setattr(flex_iq.socket, "socket", lambda *_: _Sock())
    monkeypatch.setattr(rx, "_drain", lambda *_: ["V1.4.0.0", "H7F19D8AE"])
    monkeypatch.setattr(rx, "_cmd", cmd)
    with pytest.raises(_Stop):
        rx._setup()
    assert not rx._bound and "stream remove 0x20000000" in sent and rx._stale is None
