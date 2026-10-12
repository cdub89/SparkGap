"""flex_iq.owned_objects: which radio slices and pans belong to SparkGap's own client handle."""

from flex_iq import gui_stations, owned_objects, pan_slices

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
