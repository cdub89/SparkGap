"""tools/flex_template.py reads a FlexRadio's discovery and info as the radios send them."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import flex_template as ft  # pyright: ignore[reportMissingImports]

# Dallas FLEX-6400M discovery payload, 2026-10-20 (serials left out), after a 28-byte VITA header
DISCOVERY = (
    b"\x00"
    * 28
    + b"discovery_protocol_version=3.1.0.4 model=FLEX-6400M nickname=Dallas callsign=WX7V "
    b"ip=192.0.2.134 port=4992 status=In_Use max_licensed_version=v3 licensed_clients=2 "
    b"available_clients=0 max_panadapters=2 available_panadapters=0 "
    b"max_slices=2 available_slices=0 "
    b"gui_client_programs=SparkGap,SmartSDR-Win version=4.3.16.42034\x00\x00"
)
# Lakehouse FLEX-6600 reply to info, 2026-10-20 (serial, MAC and address changed)
INFO_6600 = [
    (
        'R1|0|model="FLEX-6600",name="Lake",callsign="WX7V",gps="Not Present",'
        'atu_present=1,num_scu=2,num_slice=4,num_tx=1,software_ver=4.2.20.41343,ip=192.0.2.101,location="",options="None"'
    )
]


def test_discovery_payload_is_read_past_the_vita_header() -> None:
    kv = ft.parse_discovery(DISCOVERY)
    assert kv is not None
    assert (kv["model"], kv["nickname"], kv["ip"], kv["max_panadapters"]) == (
        "FLEX-6400M",
        "Dallas",
        "192.0.2.134",
        "2",
    )
    assert ft.parse_discovery(b"\x00" * 28 + b"something else") is None


def test_info_fields() -> None:
    info = ft.parse_info(["S0|radio slices=1", *INFO_6600])
    assert (info["model"], info["name"], info["num_slice"], info["location"]) == (
        "FLEX-6600",
        "Lake",
        "4",
        "",
    )


def test_info_only_radio_has_no_pan_or_client_counts() -> None:
    r = ft.radio_from(None, ft.parse_info(INFO_6600), "192.0.2.101")
    assert (r.model, r.nickname, r.max_slices, r.max_panadapters, r.licensed_clients) == (
        "FLEX-6600",
        "Lake",
        4,
        None,
        None,
    )
    assert r.limit() == 4 and r.sources.startswith("info")
    assert "pans not reported" in r.comment("2026-10-20 15:00Z")


def test_discovery_supplies_the_limits() -> None:
    info = ft.parse_info(
        ['R1|0|model="FLEX-6400M",name="Dallas",callsign="wx7v",num_slice=2,software_ver=4.3.16']
    )
    r = ft.radio_from(ft.parse_discovery(DISCOVERY), info, "192.0.2.134")
    assert (r.max_panadapters, r.licensed_clients, r.callsign, r.limit()) == (
        2,
        2,
        "WX7V",
        2,
    )


def test_template_takes_the_radio_and_keeps_the_example() -> None:
    example = {
        "_comment_cw_decoder": "kept",
        "sdr_type": "flex",
        "sdr_ip": "0.0.0.0",
        "callsign": "N0CALL",
        "telnet_port": 7300,
        "enable_ft8": False,
    }
    r = ft.radio_from(None, ft.parse_info(INFO_6600), "192.0.2.101")
    t = ft.build(example, r, "/run/root", "2026-10-20 15:00Z")
    assert list(t)[:2] == ["_comment_radio", "_radio"]
    assert (t["sdr_ip"], t["callsign"], t["rbn_known_dir"]) == (
        "192.0.2.101",
        "WX7V",
        "/run/root/rbn_known",
    )
    assert (
        t["_comment_cw_decoder"] == "kept" and t["enable_ft8"] is False and t["telnet_port"] == 7300
    )
    assert t["_radio"]["model"] == "FLEX-6600" and t["_radio"]["limit"] == 4
    assert "FLEX-6600 'Lake' (WX7V) at 192.0.2.101" in t["_comment_radio"]
