"""flex_iq logs every connection attempt, each failure and each successful reconnect."""

import logging

import pytest

import flex_iq
from flex_iq import FlexIQReceiver

Monkey = pytest.MonkeyPatch
Caplog = pytest.LogCaptureFixture
ATTEMPT = "[Flex] Connection attempt {} to 192.0.2.1:4992 ({})"
FAILED = "[Flex] Connection attempt {} failed: connection refused"


def _receiver(monkeypatch: Monkey, fail_times: int) -> FlexIQReceiver:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    left = [fail_times]

    def setup() -> None:
        if left[0]:
            left[0] -= 1
            raise OSError("connection refused")

    monkeypatch.setattr(rx, "_setup", setup)
    monkeypatch.setattr(rx, "_remove_objects", lambda: None)
    monkeypatch.setattr(flex_iq, "RETRY_S", 0)
    return rx


def _lines(caplog: Caplog) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "sparkgap.flex"]


def test_first_start_logs_the_attempt(monkeypatch: Monkey, caplog: Caplog) -> None:
    rx = _receiver(monkeypatch, 0)
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx.start()
    assert _lines(caplog) == [(logging.INFO, ATTEMPT.format(1, "first start"))]


def test_failed_first_start_logs_an_error_and_raises(monkeypatch: Monkey, caplog: Caplog) -> None:
    rx = _receiver(monkeypatch, 1)
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"), pytest.raises(OSError):
        rx.start()
    assert (logging.ERROR, FAILED.format(1)) in _lines(caplog)


def _no_kill(monkeypatch: Monkey) -> list[int]:
    kills: list[int] = []
    monkeypatch.setattr(flex_iq.os, "kill", lambda _pid, sig: kills.append(sig))
    return kills


def test_one_loss_gets_one_reconnect(monkeypatch: Monkey, caplog: Caplog) -> None:
    rx, kills = _receiver(monkeypatch, 0), _no_kill(monkeypatch)
    rx._running = True
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._restart("No DAX-IQ packets for 10 s", True)
    lines = _lines(caplog)
    assert lines[0] == (logging.ERROR, "[Flex] No DAX-IQ packets for 10 s: connecting again")
    assert (logging.INFO, ATTEMPT.format(1, "reconnect: No DAX-IQ packets for 10 s")) in lines
    assert lines[-1][1].startswith("[Flex] Reconnected on attempt 1, ")
    assert kills == [] and rx._running and rx.reconnects == 1


def test_failed_reconnect_stops_sparkgap(monkeypatch: Monkey, caplog: Caplog) -> None:
    rx, kills = _receiver(monkeypatch, 1), _no_kill(monkeypatch)
    rx._running = True
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._restart("No DAX-IQ packets for 10 s", True)
    lines = _lines(caplog)
    assert (logging.ERROR, FAILED.format(1)) in lines
    assert lines[-1][0] == logging.ERROR
    assert lines[-1][1].startswith("[Flex] reconnect attempt 1 failed: stopping SparkGap")
    assert kills == [flex_iq.signal.SIGINT] and not rx._running and rx.state == "halted"


def test_second_loss_within_the_window_stops_sparkgap(monkeypatch: Monkey,
                                                      caplog: Caplog) -> None:
    rx, kills = _receiver(monkeypatch, 0), _no_kill(monkeypatch)
    rx._running = True
    rx._last_loss = flex_iq.time.time() - 31
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._restart("No DAX-IQ packets for 10 s", True)
    lines = _lines(caplog)
    assert len(lines) == 1 and lines[0][0] == logging.ERROR
    assert "31 s after the previous lost connection: stopping SparkGap" in lines[0][1]
    assert kills == [flex_iq.signal.SIGINT] and rx.attempts == 0


def test_loss_after_the_window_reconnects(monkeypatch: Monkey) -> None:
    rx, kills = _receiver(monkeypatch, 0), _no_kill(monkeypatch)
    rx._running = True
    rx._last_loss = flex_iq.time.time() - flex_iq.HALT_WINDOW_S - 1
    rx._restart("No DAX-IQ packets for 10 s", True)
    assert kills == [] and rx.attempts == 1


class _ResetSocket:
    def recv(self, *_: object) -> bytes:
        raise ConnectionResetError(104, "Connection reset by peer")

    def sendall(self, *_: object) -> None:
        pass

    def close(self) -> None:
        pass


def test_connection_error_keeps_its_cause() -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp = _ResetSocket()  # pyright: ignore[reportAttributeAccessIssue]
    assert not rx._read_status()
    assert rx._lost == "Control connection error: [Errno 104] Connection reset by peer"


def test_own_disconnect_names_the_client_and_reason(caplog: Caplog) -> None:
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp, rx._handle = _ResetSocket(), "290AB860"  # pyright: ignore[reportAttributeAccessIssue]
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._close("reconnecting")
        rx._close("stopping")          # already closed: no second line
    line = "[Flex] Disconnected from 192.0.2.1 (client 0x290AB860, reconnecting)"
    assert _lines(caplog) == [(logging.INFO, line)]


def test_last_packet_time_reads_as_utc() -> None:
    assert flex_iq.utc_hms(1_792_376_071.0) == "02:14:31Z"
    assert flex_iq.utc_hms(None) == "none since connecting"


def test_reply_names_its_command_and_flags_refusals() -> None:
    sent = {7: "client gui", 12: "slice set 0 audio_mute=1"}
    ok = "[Flex] <<< R12|0| (slice set 0 audio_mute=1)"
    assert flex_iq.reply_log("R12|0|", sent) == (logging.INFO, ok)
    refused = flex_iq.reply_log("R7|F3000001|Unable to create GUI client", sent)
    text = "[Flex] <<< R7|F3000001|Unable to create GUI client (client gui)"
    assert refused == (logging.WARNING, text)
    assert sent == {}
    assert flex_iq.reply_log("R99|0|", sent) == (logging.INFO, "[Flex] <<< R99|0| (?)")
    assert flex_iq.reply_log("S1|slice 0 tx=0", sent) is None


def test_sent_commands_are_logged_at_info(caplog: Caplog) -> None:
    import socket

    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp, radio = socket.socketpair()
    with caplog.at_level(logging.INFO, logger="sparkgap.flex"):
        rx._send("sub slice all")
        radio.sendall(b"R1|0|\n")
        rx._recv_line(timeout=1.0)
    assert _lines(caplog) == [(logging.INFO, "[Flex] >>> C1|sub slice all"),
                              (logging.INFO, "[Flex] <<< R1|0| (sub slice all)")]
    radio.close()
    rx._tcp.close()


DALLAS_INFO = ('R1|0|model="FLEX-6400M",chassis_serial="0520-0945-6401-3114",name="Dallas",'
               'callsign="N0CALL",gps="Not Present",atu_present=1,num_scu=1')


def test_station_is_named_after_the_radio_nickname() -> None:
    assert flex_iq.radio_nickname([DALLAS_INFO]) == "Dallas"
    assert flex_iq.station_for("Dallas", 2) == "SparkGap-Dallas"
    assert flex_iq.station_for("Lake House", 3) == "SparkGap-Lake-House"


def test_no_nickname_falls_back_to_the_daxiq_channel() -> None:
    assert flex_iq.radio_nickname(['R1|0|model="FLEX-6600",name=""']) == ""
    assert flex_iq.radio_nickname(["R1|50000016|"]) == ""
    assert flex_iq.station_for("", 3) == "SparkGap-3"


def test_close_says_goodbye_first() -> None:
    import socket

    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000)
    rx._tcp, radio = socket.socketpair()
    rx._close("stopping")
    radio.settimeout(1.0)
    assert radio.recv(16) == b"\x04"
    radio.close()


class _Sock:
    def settimeout(self, *_: object) -> None:
        pass

    def connect(self, *_: object) -> None:
        pass


class _Stop(Exception):
    pass


def _setup_commands(monkeypatch: Monkey, listed: list[str], station: str | None = "SparkGap-1",
                    channel: int = 1, nickname: str = "Dallas") -> list[str]:
    """Commands _setup sends up to "client udpport", with `listed` as the radio's client status."""
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000, daxiq_channel=channel, station=station)
    sent: list[str] = []

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        if c.startswith("client udpport"):
            raise _Stop
        rx._seq += 1
        sent.append(c)
        if c == "info":
            return [f'R{rx._seq}|0|model="FLEX-6600",name="{nickname}",callsign="N0CALL"']
        return (listed if c == "sub client all" else []) + [f"R{rx._seq}|0|"]

    monkeypatch.setattr(flex_iq.socket, "socket", lambda *_: _Sock())
    monkeypatch.setattr(rx, "_drain", lambda *_: ["V1.4.0.0", "H7F19D8AE"])
    monkeypatch.setattr(rx, "_cmd", cmd)
    with pytest.raises(_Stop):
        rx._setup()
    return sent


def test_second_copy_binds_to_its_station_and_never_claims_the_gui(monkeypatch: Monkey) -> None:
    other = "S0|client 0x4F8B82DB connected client_id=AAAA-1 program=SparkGap station=SparkGap-1"
    sent = _setup_commands(monkeypatch, [other])
    assert "client bind client_id=AAAA-1" in sent
    assert not any(c.startswith("client gui") for c in sent)


def test_first_copy_claims_the_gui(monkeypatch: Monkey) -> None:
    smartsdr = "S0|client 0x2004D85C connected client_id=BBBB-2 program=SmartSDR-Win station=SUPER"
    sent = _setup_commands(monkeypatch, [smartsdr])
    assert any(c.startswith("client gui ") for c in sent)
    assert not any(c.startswith("client bind") for c in sent)


def test_copies_on_one_radio_share_its_station(monkeypatch: Monkey) -> None:
    first = "S0|client 0x4F8B82DB connected client_id=AAAA-1 program=SparkGap station=SparkGap-Lake"
    sent = _setup_commands(monkeypatch, [first], station=None, channel=3, nickname="Lake")
    assert sent[0] == "info"
    assert "client bind client_id=AAAA-1" in sent
    assert not any(c.startswith("client gui") for c in sent)


def test_client_id_is_the_same_for_every_copy_and_start(monkeypatch: Monkey) -> None:
    def gui_id(channel: int, nickname: str) -> str:
        sent = _setup_commands(monkeypatch, [], station=None, channel=channel, nickname=nickname)
        return next(c.split()[2] for c in sent if c.startswith("client gui "))

    a, b, other = gui_id(2, "Lake"), gui_id(3, "Lake"), gui_id(2, "Dallas")
    assert a == b != other
    assert a == a.upper() and len(a) == 36


MAESTRO = ("S0|client 0x28B5BE40 connected client_id=CCCC-3 program=SmartSDR-Maestro "
           "station=MaestroC")
LAKE_GUI = "S0|client 0x79D60253 connected client_id=AAAA-1 program=SparkGap station=SparkGap-Lake"
LAKE_PANS = [  # Lake 6600, 2026-10-19: Maestro's pan holds DAX-IQ 1, SparkGap-Lake's hold 2 and 3
    "S46CAE6B4|display pan 0x40000000 client_handle=0x28B5BE40 wnb=0 center=7.074000",
    "S46CAE6B4|display pan 0x40000000 daxiq_channel=1 ",
    "S46CAE6B4|display pan 0x40000001 client_handle=0x79D60253 wnb=0 center=14.038400",
    "S46CAE6B4|display pan 0x40000001 daxiq_channel=2 ",
    "S46CAE6B4|display pan 0x40000002 client_handle=0x79D60253 wnb=0 center=10.138400",
    "S46CAE6B4|display pan 0x40000002 daxiq_channel=3 ",
]


def test_daxiq_holders_reads_pan_status() -> None:
    assert flex_iq.daxiq_holders(LAKE_PANS, 1) == {"0x40000000": "0X28B5BE40"}
    assert flex_iq.daxiq_holders(LAKE_PANS, 3) == {"0x40000002": "0X79D60253"}
    assert flex_iq.daxiq_holders(LAKE_PANS, 4) == {}
    assert flex_iq.daxiq_holders(["S0|display pan 0x40000003 daxiq_channel=0 "], 0) == {}


def _setup_to_pan(monkeypatch: Monkey, channel: int, clients: list[str],
                  pans: list[str]) -> list[str]:
    """Commands _setup sends until it creates or sets a pan, on the Lake radio."""
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000, daxiq_channel=channel)
    sent: list[str] = []

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        if c.startswith("display pan"):
            raise _Stop
        rx._seq += 1
        sent.append(c)
        if c == "info":
            return [f'R{rx._seq}|0|model="FLEX-6600",name="Lake",callsign="N0CALL"']
        status = {"sub client all": clients, "sub pan all": pans}.get(c, [])
        return status + [f"R{rx._seq}|0|"]

    monkeypatch.setattr(flex_iq.socket, "socket", lambda *_: _Sock())
    monkeypatch.setattr(rx, "_drain", lambda *_: ["V1.4.0.0", "H7F19D8AE"])
    monkeypatch.setattr(rx, "_cmd", cmd)
    with pytest.raises(_Stop):
        rx._setup()
    return sent


def test_a_channel_another_station_holds_is_refused(monkeypatch: Monkey) -> None:
    with pytest.raises(RuntimeError, match=r"DAX-IQ 1 is in use by MaestroC \(SmartSDR-Maestro\) "
                                           r"on pan 0x40000000"):
        _setup_to_pan(monkeypatch, 1, [MAESTRO, LAKE_GUI], LAKE_PANS)


def test_a_channel_another_copy_of_our_station_streams_is_refused(monkeypatch: Monkey) -> None:
    live = LAKE_PANS + ["S79D60253|stream 0x20000011 type=dax_iq daxiq_channel=3 pan=0x40000002 "
                        "client_handle=0x79D60253 active=1"]
    with pytest.raises(RuntimeError, match=r"DAX-IQ 3 is in use by SparkGap-Lake \(SparkGap\)"):
        _setup_to_pan(monkeypatch, 3, [MAESTRO, LAKE_GUI], live)


def test_a_channel_a_bound_copy_streams_names_its_station_and_pan(monkeypatch: Monkey) -> None:
    # Dallas, 2026-10-20 22:20Z: the bound copy's stream has its own handle; the station is its GUI handle
    live = LAKE_PANS + ["S6B976AE3|stream 0x20000011 type=dax_iq daxiq_channel=3 pan=0x40000002 "
                        "client_handle=0x6B976AE3 client_gui_handle=0x79D60253 active=1"]
    with pytest.raises(RuntimeError, match=r"DAX-IQ 3 is in use by SparkGap-Lake \(SparkGap\) on pan 0x40000002;"):
        _setup_to_pan(monkeypatch, 3, [MAESTRO, LAKE_GUI], live)


def test_our_station_pan_saved_on_our_channel_is_taken_back(monkeypatch: Monkey) -> None:
    # X, 2026-10-20 11:19Z: the radio restored the station's pan on channel 3 with nobody streaming it
    sent = _setup_to_pan(monkeypatch, 3, [MAESTRO, LAKE_GUI], LAKE_PANS)
    assert "display pan create x=1 y=1" not in sent and sent[-1] == "sub daxiq all"


def test_a_channel_this_client_holds_is_ours(monkeypatch: Monkey) -> None:
    own = ["S0|display pan 0x40000003 client_handle=0x7F19D8AE",
           "S0|display pan 0x40000003 daxiq_channel=4 "]
    sent = _setup_to_pan(monkeypatch, 4, [MAESTRO, LAKE_GUI], LAKE_PANS + own)
    assert "sub pan all" in sent


def test_a_free_channel_goes_ahead(monkeypatch: Monkey) -> None:
    sent = _setup_to_pan(monkeypatch, 4, [MAESTRO, LAKE_GUI], LAKE_PANS)
    assert "sub pan all" in sent


def test_an_unknown_owner_is_named_by_handle(monkeypatch: Monkey) -> None:
    with pytest.raises(RuntimeError,
                       match=r"DAX-IQ 1 is in use by client 0X28B5BE40 on pan 0x40000000"):
        _setup_to_pan(monkeypatch, 1, [LAKE_GUI], LAKE_PANS)


Y_STREAM = [  # Y.log 2026-10-20: the owner left, the stream lost its pan, then a restored pan took it
    "S1A13B591|stream 0x20000011 type=dax_iq daxiq_channel=4 pan=0x0 slice=0x0 endpoint_type=Not Assigned "
    "daxiq_rate=96000 client_handle=0x5F91167C client_gui_handle=0x1A13B591 active=1 ip=192.168.0.100",
    "S303A65A4|stream 0x20000011 type=dax_iq daxiq_channel=4 pan=0x40000001 slice=0x0 endpoint_type=Display "
    "daxiq_rate=96000 client_handle=0x5F91167C client_gui_handle=0x303A65A4 active=1 ip=192.168.0.100",
    "S493016BC|stream 0x20000000 type=dax_iq active=0 ",
    "S493016BC|stream 0x2000000A removed ",
]


def test_daxiq_streams_reads_stream_status() -> None:
    streams = flex_iq.daxiq_streams(Y_STREAM)
    assert streams == {0x2000_0011: {"channel": "4", "pan": 0x4000_0001, "client": 0x5F91_167C, "gui": 0x303A_65A4}}
    assert flex_iq.daxiq_streams(Y_STREAM + ["S5F91167C|stream 0x20000011 removed "]) == {}
    assert flex_iq.daxiq_users(streams, 4, 0x5F91_167C, set()) == set()
    assert flex_iq.daxiq_users(streams, 4, 0x7F19_D8AE, set()) == {0x5F91_167C}


def test_an_inactive_daxiq_stream_still_holds_its_channel() -> None:
    # Maestro's DAX keeps its channel 1 streams with active=0 between uses (O1.log 2026-10-20)
    line = ("S1FCCB95D|stream 0x20000004 type=dax_iq daxiq_channel=1 pan=0x40000000 slice=0x0 "
            "endpoint_type=Display daxiq_rate=48000 client_handle=0x493016BC active=0")
    assert flex_iq.daxiq_users(flex_iq.daxiq_streams([line]), 1, 0x7F19_D8AE, set()) == {0x4930_16BC}


AETHER_GUI = "S0|client 0x6EDCDBAA connected client_id=DDDD-4 program=AetherSDR station=Fred6700"
AETHER_TCI = [  # Fred's 6700, 2026-10-20 17:5xZ: a TCI server keeps a stream on every channel, binds only one
    "S56B13ECD|stream 0x20000000 type=dax_iq daxiq_channel=1 pan=0x0 endpoint_type=Not Assigned daxiq_rate=192000 "
    "client_handle=0x56B13ECD client_gui_handle=0x6EDCDBAA active=1",
    "S56B13ECD|stream 0x20000001 type=dax_iq daxiq_channel=2 pan=0x40000000 endpoint_type=Display daxiq_rate=48000 "
    "client_handle=0x56B13ECD client_gui_handle=0x6EDCDBAA active=1",
    "S56B13ECD|stream 0x20000002 type=dax_iq daxiq_channel=3 pan=0x0 endpoint_type=Not Assigned daxiq_rate=48000 "
    "client_handle=0x56B13ECD client_gui_handle=0x6EDCDBAA active=1",
    "S56B13ECD|stream 0x20000003 type=dax_iq daxiq_channel=4 pan=0x0 endpoint_type=Not Assigned daxiq_rate=48000 "
    "client_handle=0x56B13ECD client_gui_handle=0x6EDCDBAA active=1",
]


def test_only_a_bound_stream_or_a_sparkgap_copys_claims_a_channel() -> None:
    streams = flex_iq.daxiq_streams(AETHER_TCI)
    me = 0x7F19_D8AE
    assert [flex_iq.daxiq_users(streams, c, me, set()) for c in (1, 2, 3, 4)] == [set(), {0x56B1_3ECD}, set(), set()]
    assert flex_iq.daxiq_users(streams, 3, me, {0x6EDC_DBAA}) == {0x56B1_3ECD}


def test_a_channel_another_program_streams_unbound_goes_ahead(monkeypatch: Monkey) -> None:
    # the radio let a copy stream channel 3 next to the TCI server's unbound stream for 30 min (Fred, 10-20)
    sent = _setup_to_pan(monkeypatch, 3, [AETHER_GUI, LAKE_GUI], LAKE_PANS + AETHER_TCI)
    assert "display pan create x=1 y=1" not in sent and sent[-1] == "sub daxiq all"   # saved pan taken back


def test_a_channel_another_program_binds_is_refused(monkeypatch: Monkey) -> None:
    with pytest.raises(RuntimeError, match=r"DAX-IQ 2 is in use by Fred6700 \(AetherSDR\) on pan 0x40000000;"):
        _setup_to_pan(monkeypatch, 2, [AETHER_GUI, LAKE_GUI], LAKE_PANS + AETHER_TCI)


def test_a_channel_another_copy_streams_before_its_bind_is_refused(monkeypatch: Monkey) -> None:
    # a bound copy's new stream has no pan until it binds; its station's GUI client is SparkGap
    live = LAKE_PANS + ["S6B976AE3|stream 0x20000011 type=dax_iq daxiq_channel=3 pan=0x0 "
                        "client_handle=0x6B976AE3 client_gui_handle=0x79D60253 active=1"]
    with pytest.raises(RuntimeError, match=r"DAX-IQ 3 is in use by SparkGap-Lake \(SparkGap\) on pan none;"):
        _setup_to_pan(monkeypatch, 3, [MAESTRO, LAKE_GUI], live)

def test_pan_status_keeps_saved_channels_and_drops_removed_pans() -> None:
    lines = LAKE_PANS + ["S0|display pan 0x40000003 center=7.038400", "S0|display pan 0x40000001 removed"]
    assert flex_iq.pan_status(lines) == {0x4000_0000: "1", 0x4000_0002: "3", 0x4000_0003: None}


def _setup_refused(monkeypatch: Monkey, refused: str) -> list[str]:
    """Run _setup on a radio that accepts everything except commands starting with `refused`."""
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000, daxiq_channel=4)
    sent: list[str] = []
    ids = {"display pan create": "0x40000003,0x42000003", "slice create": "3",
           "stream create": "0x20000011"}

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        rx._seq += 1
        sent.append(c)
        if c.startswith(refused):
            return [f"R{rx._seq}|50000029|"]
        if c == "info":
            return [f'R{rx._seq}|0|model="FLEX-6600",name="Lake"']
        if c == "sub client all":
            return [MAESTRO, f"R{rx._seq}|0|"]
        if c == "sub slice all":   # the radio's default slice on the new pan
            return ["S0|slice 3 in_use=1 pan=0x40000003 client_handle=0x7F19D8AE", f"R{rx._seq}|0|"]
        made = next((v for k, v in ids.items() if c.startswith(k)), "")
        return [f"R{rx._seq}|0|{made}"]

    monkeypatch.setattr(flex_iq.socket, "socket", lambda *_: _Sock())
    monkeypatch.setattr(rx, "_drain", lambda *_: ["V1.4.0.0", "H7F19D8AE"])
    monkeypatch.setattr(rx, "_cmd", cmd)
    with pytest.raises(RuntimeError, match="refused"):
        rx._setup()
    assert rx.state != "live" and not rx._pan_bound
    return sent


def test_a_refused_pan_centre_fails_the_start(monkeypatch: Monkey) -> None:
    sent = _setup_refused(monkeypatch, "display pan set 0x40000003 center=")
    assert not any(c.startswith("stream create") for c in sent)


def test_a_refused_slice_tune_fails_the_start(monkeypatch: Monkey) -> None:
    _setup_refused(monkeypatch, "slice tune")


def test_a_refused_daxiq_bind_fails_the_start(monkeypatch: Monkey) -> None:
    sent = _setup_refused(monkeypatch, "display pan set 0x40000003 daxiq_channel=")
    assert not any(c.startswith("stream set") for c in sent)


def test_the_owner_leaves_pans_other_copies_stream(monkeypatch: Monkey) -> None:
    # O2, 2026-10-20 11:21Z: restored pans on channels 3 and 4; Y's live stream feeds the channel 4 one
    rx = FlexIQReceiver("192.0.2.1", sample_rate=96_000, daxiq_channel=2)
    sent: list[str] = []
    own = "client_handle=0x7F19D8AE"
    pans = [f"S0|display pan 0x40000001 {own} center=7.038400", "S0|display pan 0x40000001 daxiq_channel=4 ",
            f"S0|display pan 0x40000002 {own} center=7.038400", "S0|display pan 0x40000002 daxiq_channel=2 ",
            f"S0|display pan 0x40000003 {own} center=3.538400", "S0|display pan 0x40000003 daxiq_channel=3 ",
            f"S0|display pan 0x40000004 {own} center=14.100000"]    # a copy still starting: no stream yet
    slices = [f"S0|slice {n} in_use=1 pan=0x4000000{n} {own}" for n in (1, 2, 3, 4)]

    def cmd(c: str, timeout: float = 2.0, settle: float = 0.0) -> list[str]:
        if c.startswith("stream create"):
            raise _Stop
        rx._seq += 1
        sent.append(c)
        if c == "info":
            return [f'R{rx._seq}|0|model="FLEX-6600",name="Lake"']
        status = {"sub client all": [MAESTRO], "sub pan all": pans, "sub slice all": slices,
                  "sub daxiq all": Y_STREAM}.get(c, [])
        return status + [f"R{rx._seq}|0|"]

    monkeypatch.setattr(flex_iq.socket, "socket", lambda *_: _Sock())
    monkeypatch.setattr(rx, "_drain", lambda *_: ["V1.4.0.0", "H7F19D8AE"])
    monkeypatch.setattr(rx, "_cmd", cmd)
    with pytest.raises(_Stop):
        rx._setup()
    assert rx._pan_id == 0x4000_0002 and rx._slice_id == 2    # its own saved pan back
    assert [c for c in sent if "remove" in c] == ["display pan remove 0x40000003"]   # its slice goes with it
