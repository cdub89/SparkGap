"""flex_iq.owned_objects: which radio slices and pans belong to SparkGap's own client handle."""

from flex_iq import FlexIQReceiver, gui_stations, owned_objects, pan_slices

STATUS = [
    # SparkGap's default objects (the radio's Slice A on the 40m phone default)
    "S3CBB9746|slice 1 in_use=1 RF_frequency=7.175000 client_handle=0x3CBB9746 dax=1",
    "S3CBB9746|display pan 0x40000000 center=7.175000 client_handle=0x3CBB9746",
    # another station (SmartSDR with CW Skimmer): must never be touched
    "S3CBB9746|slice 0 in_use=1 RF_frequency=7.053000 client_handle=0x1A2B3C4D dax_iq=1",
    "S3CBB9746|display pan 0x40000002 center=7.053000 client_handle=0x1A2B3C4D",
    # a freed slice slot still reported for our handle
    "S3CBB9746|slice 2 in_use=0 client_handle=0x3CBB9746",
    "R5|0|",
]


def test_only_own_objects_are_returned() -> None:
    assert owned_objects(STATUS, "3CBB9746") == ({1}, {"0x40000000"})


def test_handle_prefix_of_another_handle_does_not_match() -> None:
    line = "S1|slice 3 in_use=1 client_handle=0x3CBB97461"
    assert owned_objects([line], "3CBB9746") == (set(), set())


def test_no_handle_frees_nothing() -> None:
    assert owned_objects(STATUS, "") == (set(), set())


def test_cmd_returns_at_its_own_reply() -> None:
    import socket
    import time

    from flex_iq import FlexIQReceiver

    rx = FlexIQReceiver("127.0.0.1", freq_hz=7_030_000, sample_rate=96_000)
    ours, radio = socket.socketpair()
    rx._tcp = ours
    radio.sendall(b"S1|slice 0 in_use=1\nR0|0|\nR1|0|7\nS1|display pan 0x40000000\n")
    t0 = time.time()
    lines = rx._cmd("slice create", timeout=2.0)
    assert time.time() - t0 < 0.5
    assert lines[-1] == "R1|0|7"
    assert rx._cmd("sub pan all", timeout=0.3, settle=0.1)[-1] == "S1|display pan 0x40000000"
    ours.close()
    radio.close()


def test_slice_pans_maps_own_slices_to_their_pan() -> None:
    from flex_iq import slice_pans

    lines = [
        "S1|slice 1 in_use=1 pan=0x40000001 client_handle=0x3CBB9746 RF_frequency=14.1",
        "S1|slice 0 in_use=1 pan=0x40000000 client_handle=0x1A2B3C4D RF_frequency=7.053",
    ]
    assert slice_pans(lines, "3CBB9746") == {1: "0x40000001"}


CLIENTS = [
    "S3BEDC9D2|client 0x73E33F06 connected client_id=8A1B6F0E-0000-4000-8000-000000000001 "
    "program=SmartSDR-Win station=BEELINK local_ptt=1",
    "S3BEDC9D2|client 0x1EFAD774 connected client_id=5C2D7A11-0000-4000-8000-000000000002 "
    "program=SparkGap station=SparkGap-2 local_ptt=0",
    "S3BEDC9D2|client 0x3BEDC9D2 connected program=SparkGap station=",     # non-GUI: no client_id
    "R7|0|",
]


def test_gui_stations_lists_clients_with_an_id() -> None:
    assert gui_stations(CLIENTS) == [
        ("0x73E33F06", "8A1B6F0E-0000-4000-8000-000000000001", "SmartSDR-Win", "BEELINK"),
        ("0x1EFAD774", "5C2D7A11-0000-4000-8000-000000000002", "SparkGap", "SparkGap-2"),
    ]


def test_pan_slices_finds_slices_on_one_pan_whoever_owns_them() -> None:
    lines = [
        "S1|slice 0 in_use=1 pan=0x40000000 client_handle=0x73E33F06",
        "S1|slice 2 in_use=1 pan=0x40000002 client_handle=0x1EFAD774",
        "S1|slice 3 in_use=0 pan=0x40000002 client_handle=0x1EFAD774",
    ]
    assert pan_slices(lines, 0x40000002) == {2}
    assert pan_slices(lines, 0x40000003) == set()


def test_clients_gone_picks_the_disconnect_lines() -> None:
    from flex_iq import clients_gone

    gone = "S1|client 0x39795134 disconnected forced=1"
    assert clients_gone(CLIENTS + [gone]) == [gone]


def test_ptt_ours_only_when_a_sparkgap_station_holds_local_ptt() -> None:
    from flex_iq import ptt_ours

    ours = "S1|client 0x1372089D connected local_ptt=1 program=SparkGap station=SparkGap-2"
    lines = ["S1|client 0x26DB5920 connected local_ptt=1 program=SmartSDR-Win station=BEELINK",
             "S1|client 0x1372089D connected local_ptt=0 program=SparkGap station=SparkGap-2"]
    assert ptt_ours(lines) == []
    assert ptt_ours(lines + [ours]) == [ours]


def test_read_status_empties_the_connection_and_sees_it_close() -> None:
    import socket

    from flex_iq import FlexIQReceiver

    rx = FlexIQReceiver("127.0.0.1", freq_hz=7_030_000, sample_rate=96_000)
    rx._tcp, radio = socket.socketpair()
    rx._tcp.settimeout(2.0)
    radio.sendall(b"S1|interlock state=TRANSMITTING\n" * 2_000 + b"S1|slice 0 tx")
    while rx._buf != b"S1|slice 0 tx":       # every whole line read and dropped, the part line kept
        assert rx._read_status()
    assert rx._read_status()                 # nothing waiting: still connected
    radio.close()
    assert not rx._read_status()
    assert rx._lost == "The radio closed the control connection"
    rx._close("stopping")
    assert rx._tcp is None


def test_station_name_follows_the_channel_unless_set() -> None:
    assert FlexIQReceiver("0.0.0.0", daxiq_channel=2).station == "SparkGap-2"
    assert FlexIQReceiver("0.0.0.0", daxiq_channel=2, station="SparkGap-1").station == "SparkGap-1"


def test_failed_start_removes_what_it_made() -> None:
    rx = FlexIQReceiver("0.0.0.0", daxiq_channel=3)
    sent: list[str] = []

    def setup() -> None:
        rx._pan_id, rx._slice_id = 0x40000001, 1
        raise RuntimeError("[Flex] Failed to create DAX-IQ stream")

    rx._setup = setup  # type: ignore[method-assign]
    rx._cmd = lambda cmd, timeout=2.0, settle=0.0: sent.append(cmd) or ["R1|0|"]  # type: ignore[method-assign]
    try:
        rx.start()
    except RuntimeError:
        pass
    assert sent == ["slice remove 1", "display pan remove 0x40000001"]


def test_start_forgets_objects_from_a_dropped_connection() -> None:
    rx = FlexIQReceiver("0.0.0.0", daxiq_channel=2)
    rx._stream_id, rx._pan_id, rx._slice_id = 0x20000000, 0x40000000, 0
    sent: list[str] = []

    def setup() -> None:
        raise OSError("radio unreachable")

    rx._setup = setup  # type: ignore[method-assign]
    rx._cmd = lambda cmd, timeout=2.0, settle=0.0: sent.append(cmd) or ["R1|0|"]  # type: ignore[method-assign]
    try:
        rx.start()
    except OSError:
        pass
    assert sent == []
