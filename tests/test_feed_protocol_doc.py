"""docs/GUI_FEED_PROTOCOL.md matches what the feed sends: message fields and the spectrum layout."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

import feed_server
from feed_server import FEED_VERSION, PROTOCOL, SPECTRUM_HEADER, FeedServer
from sparkgap import _start_gui_feed

DOC = (Path(__file__).resolve().parent.parent / "docs" / "GUI_FEED_PROTOCOL.md").read_text()


def doc_examples() -> dict[str, dict[str, Any]]:
    """The doc's JSON examples by type; client messages included."""
    out = {}
    for block in re.findall(r"```json\n(.*?)```", DOC, re.S):
        msg = json.loads(block)
        out[msg["type"]] = msg
    return out


EXAMPLES = doc_examples()


@pytest.fixture
def feed():
    f = FeedServer({"bind": "127.0.0.1", "port": 0}, {})
    f.start()
    yield f
    f.stop()


def sent(feed: FeedServer, kind: str) -> dict[str, Any]:
    if kind == "window":
        feed.publish_window(7021.2, "CQ DE W1AW W1AW K", 22, 18.0, 0.4)
        kept = feed.windows
    else:
        feed.publish_spot(7021.2, "W1AW", 18.0, 22)
        kept = feed.spots
    t = time.monotonic()
    while not kept:
        assert time.monotonic() - t < 3
        time.sleep(0.01)
    return kept[-1]


@pytest.mark.parametrize("kind", ["window", "spot"])
def test_live_message_fields(feed: FeedServer, kind: str) -> None:
    assert set(EXAMPLES[kind]) == set(sent(feed, kind))


def test_snapshot_fields(feed: FeedServer) -> None:
    sent(feed, "window")
    sent(feed, "spot")
    snap, doc = feed.snapshot(), EXAMPLES["snapshot"]
    assert set(doc) == set(snap)
    assert set(doc["signals"][0]) == set(snap["signals"][0])
    assert set(doc["windows"][0]) == set(snap["windows"][0])
    assert set(doc["spots"][0]) == set(snap["spots"][0])


def test_status_fields() -> None:
    # the status dict is built in sparkgap._publish_gui_status from ProcessStats
    keys = set(feed_server.ProcessStats().sample()) | {"bins", "spots", "uptime_s", "type", "utc"}
    assert set(EXAMPLES["status"]) == keys


def test_hello_fields() -> None:
    import sparkgap

    cfg = {"gui_feed": {"bind": "127.0.0.1", "port": 0}, "callsign": "W1AW"}
    _start_gui_feed(cfg, [("40m", 7_048_000.0)], 96_000, [])
    try:
        hello, doc = sparkgap._gui_feed.hello, EXAMPLES["hello"]
        assert set(doc) == set(hello)
        assert set(doc["config"]) == set(hello["config"])
        assert set(doc["bands"][0]) == set(hello["bands"][0])
        assert doc["v"] == FEED_VERSION
    finally:
        sparkgap._gui_feed.stop()
        sparkgap._gui_feed = None


def test_spectrum_layout() -> None:
    rows = re.findall(r"^\| (\d+) \| (u32|f64|f32|u16|u8[^|]*) \|", DOC, re.M)
    offsets = [int(o) for o, _ in rows]
    codes = {"u32": "I", "f64": "d", "f32": "f", "u16": "H"}
    fmt = "<" + "".join(codes[t] for _, t in rows if t in codes)
    assert fmt == SPECTRUM_HEADER.format
    assert offsets[-1] == SPECTRUM_HEADER.size


def test_names() -> None:
    assert f"Protocol `{PROTOCOL}`, feed version {FEED_VERSION}." in DOC
    assert EXAMPLES["subscribe"] == {"type": "subscribe", "spectrum_hz": 20}
