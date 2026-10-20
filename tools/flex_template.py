#!/usr/bin/env python3
"""Write a SparkGap config template from skimmer_flex.example.json and what a FlexRadio reports.

Read-only towards the radio: it listens for the radio's discovery broadcast (UDP 4992) and
connects as a plain client that sends only "info",
as FlexLib's own clients do before they register. No GUI client, pan, slice or stream is made.

Usage: flex_template.py --example <example json> --out <template json> --root <run root>
                        [--address <radio address>]
"""

import argparse
import json
import re
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PORT = 4992  # discovery (UDP) and commands (TCP) share it
DISCOVERY_S = 5.0  # a radio broadcasts about once a second
GREETING_S = 1.0  # time for the radio's greeting after connecting
DAXIQ_CHANNELS = 4  # DAX-IQ channels 1 to 4 on every 6000 and 8000 series radio

KV = re.compile(r'([A-Za-z_0-9]+)=("[^"]*"|\S*)')


def pairs(text: str) -> dict[str, str]:
    """key=value pairs in a discovery payload or a reply, quotes taken off."""
    return {k: v.strip('"') for k, v in KV.findall(text)}


def parse_discovery(packet: bytes) -> dict[str, str] | None:
    """The key=value payload of a VITA discovery packet, or None for anything else."""
    text = packet.decode("ascii", "replace")
    start = text.find("discovery_protocol_version=")
    if start < 0:
        return None
    kv = pairs(text[start:].replace("\x00", " "))
    return kv if kv.get("ip") and kv.get("model") else None


def parse_info(lines: list[str]) -> dict[str, str]:
    """The fields of the radio's reply to "info" (R<seq>|0|model="FLEX-6600",...)."""
    for line in lines:
        m = re.match(r"R\d+\|0\|(.*)", line)
        if m and "model=" in m.group(1):
            return pairs(m.group(1).replace(",", " "))
    return {}


def num(v: str | None) -> int | None:
    return int(v) if v is not None and v.isdigit() else None


@dataclass
class Radio:
    address: str
    model: str
    nickname: str
    callsign: str
    software: str
    max_slices: int | None
    max_panadapters: int | None
    licensed_clients: int | None
    scu: int | None
    sources: str

    def limit(self) -> int:
        """Most SparkGap copies the radio holds: a pan, a slice and a DAX-IQ channel each."""
        caps = [c for c in (self.max_panadapters, self.max_slices) if c]
        return min(caps + [DAXIQ_CHANNELS])

    def comment(self, when: str) -> str:
        def d(v: int | None, what: str) -> str:
            return f"{v} {what}" if v is not None else f"{what} not reported"

        return (
            f"Made by sparkgap.sh template {when} from {self.model} '{self.nickname}' "
            f"({self.callsign}) "
            f"at {self.address}, software {self.software}: {d(self.max_slices, 'slices')}, "
            f"{d(self.max_panadapters, 'pans')}, {d(self.licensed_clients, 'GUI clients')}; "
            f"room for {self.limit()} SparkGap copies. "
            f"From {self.sources}."
        )


def radio_from(disc: dict[str, str] | None, info: dict[str, str], address: str) -> Radio:
    disc = disc or {}
    return Radio(
        address=address,
        model=info.get("model") or disc.get("model", "?"),
        nickname=info.get("name") or disc.get("nickname", ""),
        callsign=(info.get("callsign") or disc.get("callsign", "")).upper(),
        software=info.get("software_ver") or disc.get("version", "?"),
        max_slices=num(disc.get("max_slices")) or num(info.get("num_slice")),
        max_panadapters=num(disc.get("max_panadapters")),
        licensed_clients=num(disc.get("licensed_clients")),
        scu=num(info.get("num_scu")),
        sources="discovery and info" if disc else "info (no discovery heard)",
    )


def build(example: dict, radio: Radio, root: str, when: str) -> dict:
    """The template: the example's settings, this radio's address and callsign, and what it is."""
    out = {
        "_comment_radio": radio.comment(when),
        "_radio": {
            "model": radio.model,
            "nickname": radio.nickname,
            "address": radio.address,
            "software": radio.software,
            "max_slices": radio.max_slices,
            "max_panadapters": radio.max_panadapters,
            "licensed_clients": radio.licensed_clients,
            "scu": radio.scu,
            "limit": radio.limit(),
            "asked": when,
        },
    }
    out.update({k: v for k, v in example.items() if k not in out})
    out.update(
        sdr_type="flex",
        sdr_ip=radio.address,
        callsign=radio.callsign,
        rbn_known_dir=f"{root}/rbn_known",
    )
    return out


def discover(seconds: float) -> dict[str, dict[str, str]] | None:
    """Radios heard on the discovery port, by address; None when the port cannot be opened here."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", PORT))
    except OSError:
        return None
    heard: dict[str, dict[str, str]] = {}
    end = time.monotonic() + seconds
    with s:
        while (left := end - time.monotonic()) > 0:
            s.settimeout(left)
            try:
                data, _ = s.recvfrom(65_535)
            except TimeoutError:
                break
            kv = parse_discovery(data)
            if kv:
                heard[kv["ip"]] = kv
    return heard


def ask(address: str) -> list[str]:
    """The radio's reply to "info", over a plain client connection."""
    with socket.create_connection((address, PORT), timeout=10) as s:
        s.settimeout(0.3)
        buf = b""

        def read(until: str | None, seconds: float) -> list[str]:
            nonlocal buf
            got: list[str] = []
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                try:
                    chunk = s.recv(65_536)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                *lines, buf = (buf + chunk).split(b"\n")
                got += [line.decode("utf-8", "replace").strip() for line in lines]
                if until and any(line.startswith(until) for line in got):
                    break
            return got

        read(None, GREETING_S)  # version, handle and the radio's status
        s.sendall(b"C1|info\n")
        info = read("R1|", 5.0)
        s.sendall(b"\x04")  # FlexLib tells the radio it is leaving before it closes
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--example", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--address")
    a = ap.parse_args()
    heard = discover(DISCOVERY_S)
    if a.address:
        address = a.address
    elif heard is None:
        print(
            "The discovery port is taken here (on Windows, SmartSDR or DAX hold it): "
            "give the radio's address."
        )
        return 2
    elif len(heard) == 1:
        address = next(iter(heard))
    else:
        found = ", ".join(
            f"{ip} ({kv.get('model')} '{kv.get('nickname', '')}')"
            for ip, kv in sorted(heard.items())
        )
        what = f"No radio heard in {DISCOVERY_S:.0f} s" if not heard else f"Radios: {found}"
        print(f"{what}: give the radio's address.")
        return 2
    try:
        info = ask(address)
    except OSError as e:
        print(f"No answer from {address}:{PORT}: {e}")
        return 2
    fields = parse_info(info)
    if not fields:
        print(f"{address} did not answer info like a FlexRadio")
        return 2
    when = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    radio = radio_from((heard or {}).get(address), fields, address)
    tpl = build(json.loads(a.example.read_text()), radio, a.root, when)
    if a.out.exists():  # kept, never overwritten
        old = a.out.with_name(f"{a.out.name}.{time.strftime('%Y%m%d_%H%M%SZ', time.gmtime())}")
        a.out.rename(old)
        print(f"{a.out}: the old one is now {old.name}")
    tmp = a.out.with_name(a.out.name + ".new")
    tmp.write_text(json.dumps(tpl, indent=2) + "\n")
    tmp.rename(a.out)
    print(f"{a.out}: {tpl['_comment_radio']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
