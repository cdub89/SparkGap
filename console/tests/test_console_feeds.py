"""Console server list: URL handling, checks, and the list kept in QSettings."""

import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtWebSockets")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import (
    SPECTRUM_HEADER,
    check_server,
    feed_url,
    host_port,
    load_servers,
    merge_servers,
    open_request,
    save_servers,
    servers_from_feeds,
    url_parts,
)
from PySide6.QtCore import QSettings
from sparkgap_console import parse


def test_localhost_opens_ipv4_loopback() -> None:
    # saved and shown as typed; only the connection goes to 127.0.0.1
    assert feed_url("localhost", 7320) == "ws://localhost:7320/feed"
    assert open_request("ws://localhost:7320/feed", "").url().toString() == "ws://127.0.0.1:7320/feed"
    assert open_request("ws://LocalHost:7321/feed", "").url().toString() == "ws://127.0.0.1:7321/feed"
    assert open_request("ws://beelink:7320/feed", "").url().toString() == "ws://beelink:7320/feed"


def test_url_round_trip() -> None:
    url = feed_url(" 127.0.0.1 ", 7321)
    assert url == "ws://127.0.0.1:7321/feed"
    assert url_parts(url) == ("127.0.0.1", 7321)
    assert url_parts("ws://127.0.0.1/feed") == ("127.0.0.1", 7320)


@pytest.mark.parametrize(
    ("address", "ok"),
    [
        ("127.0.0.1", True),
        ("beelink", True),
        ("beelink:9000", True),  # one feed outside the searched range
        ("beelink:7321", False),  # inside it: leave the port off, every port there is searched
        ("", False),
        ("ws://127.0.0.1", False),
        ("local host", False),
        ("beelink:x", False),
        ("beelink:0", False),
        ("beelink:70000", False),
    ],
)
def test_check_server(address: str, ok: bool) -> None:
    assert (check_server(address) == "") is ok


@pytest.mark.parametrize(
    ("code", "ok"),
    [("", True), ("XXXXXX", True), ("xxx-xxx", True), (" xxx xxx ", True),
     ("XXXXX", False), ("XXXXXXX", False), ("XXXXXO", False), ("XXXXX1", False)],
)
def test_check_join_code(code: str, ok: bool) -> None:
    assert (check_server("127.0.0.1", code) == "") is ok


def test_host_port() -> None:
    assert host_port(" beelink ") == ("beelink", 0)
    assert host_port("beelink:9000") == ("beelink", 9000)


def test_save_load_round_trip(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
    servers = [("beelink", "TOK111"), ("127.0.0.1", "")]
    save_servers(store, servers)
    assert load_servers(QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)) == servers
    save_servers(store, servers[:1])  # a shorter list leaves no stale rows behind
    assert load_servers(store) == servers[:1]


def test_old_per_port_list_becomes_servers(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
    old = [("ws://beelink:7320/feed", "AAAAAA"), ("ws://beelink:7321/feed", "AAAAAA"),
           ("ws://rednuc:7320/feed", "BBBBBB"), ("ws://far:9000/feed", "")]
    store.beginWriteArray("connections", len(old))
    for i, (url, token) in enumerate(old):
        store.setArrayIndex(i)
        store.setValue("url", url)
        store.setValue("token", token)
    store.endArray()
    assert load_servers(store) == [("beelink", "AAAAAA"), ("rednuc", "BBBBBB"), ("far:9000", "")]


def test_merge_keeps_order_and_takes_newer_token() -> None:
    saved = [("a", "old"), ("b", "x")]
    extra = [("a", "new"), ("c", "y")]
    assert merge_servers(saved, extra) == [("a", "new"), ("b", "x"), ("c", "y")]


def test_parse_normalises_to_saved_form() -> None:
    assert parse(["127.0.0.1:7321#xxx-xxx"]) == [("ws://127.0.0.1:7321/feed", "XXXXXX")]
    assert parse(["ws://127.0.0.1/feed"]) == [("ws://127.0.0.1:7320/feed", "")]
    assert parse([]) == []
    assert servers_from_feeds(parse(["127.0.0.1:7321#xxx-xxx", "far:9000"])) == [
        ("127.0.0.1", "XXXXXX"), ("far:9000", "")]


def test_wrong_code_is_not_retried() -> None:
    # a retry with the same wrong code would only walk the console into the feed's lockout
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend import Instance
    from PySide6.QtCore import QCoreApplication, QTimer

    import feed_server


    app = QCoreApplication.instance() or QCoreApplication([])
    f = feed_server.FeedServer({"bind": "127.0.0.1", "port": 0, "join_code": "XXXXXX"}, {})
    f.start()
    f.started.wait(5)
    try:
        inst = Instance(f"ws://127.0.0.1:{f.port}/feed", "YYYYYY")
        inst.open()
        QTimer.singleShot(2500, app.quit)
        app.exec()
        assert inst.state == "refused" and inst.error == "wrong join code"
        assert not inst.retry_timer.isActive() and len(f.misses["127.0.0.1"]) == 1
        inst.close()
    finally:
        f.stop()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01\x00",
        SPECTRUM_HEADER.pack(2, 0.0, 7.0e6, 50.0, 0.25, 4) + bytes(4),  # unknown version
        SPECTRUM_HEADER.pack(1, 0.0, 7.0e6, 50.0, 0.25, 8) + bytes(4),  # shorter than it says
        SPECTRUM_HEADER.pack(1, 0.0, 7.0e6, 50.0, 0.25, 0),  # no bins
        SPECTRUM_HEADER.pack(1, float("nan"), 7.0e6, 50.0, 0.25, 4) + bytes(4),
        SPECTRUM_HEADER.pack(1, 0.0, 7.0e6, 0.0, 0.25, 4) + bytes(4),  # no bin width
    ],
)
def test_malformed_spectrum_rows_ignored(data: bytes) -> None:
    from backend import Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    inst = Instance("ws://127.0.0.1:9/feed")
    inst._on_binary(data)
    assert inst.n_bins == 0 and not inst.pending
    good = SPECTRUM_HEADER.pack(1, 0.0, 7.0e6, 50.0, 0.25, 4) + bytes(4)
    inst._on_binary(good)
    assert inst.n_bins == 4 and len(inst.pending) == 1


def test_spot_on_selection_refreshes_detail() -> None:
    from backend import Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    inst: Any = Instance("ws://127.0.0.1:9/feed")  # Qt properties type as Property
    inst._on_text('{"type": "window", "khz": 7021.2, "text": "CQ W1AW W1AW", "utc": 1.0}')
    inst.select(7021.2, "W1AW")
    assert inst.detail["spotted"] is False
    seen = []
    inst.detailChanged.connect(lambda: seen.append(inst.detail["spotted"]))
    inst._on_text('{"type": "spot", "khz": 7021.2, "call": "W1AW", "utc": 2.0}')
    assert seen == [True]


def test_hello_forgets_recent_text() -> None:
    from backend import Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    inst = Instance("ws://127.0.0.1:9/feed")
    for t in (1.0, 5.0):
        inst._on_text(f'{{"type": "window", "khz": 7021.2, "text": "CQ W1AW W1AW", "utc": {t}}}')
    assert inst.guess_call(7021.2) == "W1AW"
    inst._on_text('{"type": "hello", "v": 1}')  # a reconnect: the server's state may be new
    assert inst.guess_call(7021.2) == ""


def test_demo_is_a_view(tmp_path: Path) -> None:
    from backend import Console
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
    c: Any = Console([("127.0.0.1:9", "")], store)
    server = c._add("ws://127.0.0.1:9/feed", c._servers[0])
    c.setDemo(True)
    assert c.demo and c.current.demo and c.current is not server
    assert server in c.instances and not server.closed  # still connected behind the demo
    c._save()
    assert load_servers(store) == [("127.0.0.1:9", "")]  # demo is never saved
    c.setDemo(False)
    assert not c.demo and c.current is server and len(c.instances) == 1
    server.close()


def test_station_name_has_no_address() -> None:
    from backend import Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    inst: Any = Instance("ws://100.64.0.1:7320/feed")
    assert inst.station == "" and inst.title == "100.64.0.1:7320"  # Settings still has the address
    inst._on_text('{"type": "hello", "v": 1, "host": "shack-pc",'
                  ' "bands": [{"name": "20m", "center_khz": 14048.0, "span_khz": 96.0}]}')
    assert inst.station == "shack-pc · 20m" and inst.title == "shack-pc · 20m"


def test_instance_config_names_the_server() -> None:
    from backend import Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    hello = ('{"type": "hello", "v": 1, "host": "shack-pc", "run": {"config": "%s"},'
             ' "bands": [{"name": "20m", "center_khz": 14048.0, "span_khz": 96.0}]}')
    inst: Any = Instance("ws://100.64.0.1:7320/feed")
    inst._on_text(hello % "/srv/sparkgap/lakehouse1.local.json")
    assert inst.title == "lakehouse1 · 20m"
    inst._on_text(hello % "sk_5band.json")
    assert inst.title == "shack-pc · 20m"  # not named after an instance: the host


def test_scan_report() -> None:
    from backend import scan_report

    assert scan_report({7320: "ok", 7321: "ok", 7322: "code", 7323: "none"}) == (
        "Found 2 SparkGaps. Different join code on 1.")
    assert scan_report({7320: "none"}) == "No SparkGap answered at that address."


def _feeds(codes: list[str], names: list[str]) -> list[Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import feed_server

    out = []
    for code, name in zip(codes, names, strict=True):
        hello = {"run": {"config": f"/x/config/{name}.local.json"},
                 "bands": [{"name": "20m", "center_khz": 14048.0, "span_khz": 96.0}]}
        f = feed_server.FeedServer({"bind": "127.0.0.1", "port": 0, "join_code": code}, hello)
        f.start()
        f.started.wait(5)
        out.append(f)
    return out


def _wait(app: Any, cond: Any, s: float = 8.0) -> None:
    from PySide6.QtCore import QElapsedTimer

    t = QElapsedTimer()
    t.start()
    while not cond() and t.elapsed() < s * 1000:
        app.processEvents()


def test_a_server_finds_every_copy_with_its_code(tmp_path: Path, monkeypatch: Any) -> None:
    # one address and code finds each SparkGap there; a copy with another code is reported and
    # left out of later automatic searches, so a stale code never earns the feed's lockout
    import backend
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    feeds = _feeds(["XXXXXX", "XXXXXX", "YYYYYY"], ["lake2", "lake3", "other"])
    try:
        monkeypatch.setattr(backend, "SCAN_PORTS", [f.port for f in feeds])
        store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
        c: Any = backend.Console([], store)
        done: list[tuple[bool, str]] = []
        c.scanDone.connect(lambda added, text: done.append((added, text)))
        assert c.saveServer(-1, "127.0.0.1", "xxx-xxx") == ""
        _wait(app, lambda: bool(done))
        assert done == [(True, "Found 2 SparkGaps. Different join code on 1.")]
        assert load_servers(store) == [("127.0.0.1", "XXXXXX")]  # no port, code normalised
        ports = sorted(url_parts(i.feed[0])[1] for i in c.instances)
        assert ports == sorted(f.port for f in feeds[:2])
        assert c._servers[0].refused == {feeds[2].port}
        assert c.saveServer(-1, "127.0.0.1", "") == "already in the list"
        c._scan(c._servers[0])  # an automatic search skips the port that refused the code
        _wait(app, lambda: not c._servers[0].scanning)
        assert len(feeds[2].misses["127.0.0.1"]) == 1
        c.removeServer(0)
        assert load_servers(store) == [] and c.current is None
    finally:
        for f in feeds:
            f.stop()


def test_the_shown_copy_is_followed_by_name(tmp_path: Path, monkeypatch: Any) -> None:
    # the copies restart in the other order and swap ports: the console still shows the same one
    import backend
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    feeds = _feeds(["XXXXXX", "XXXXXX"], ["lake2", "lake3"])
    try:
        monkeypatch.setattr(backend, "SCAN_PORTS", [f.port for f in feeds])
        store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
        c: Any = backend.Console([], store)
        c.saveServer(-1, "127.0.0.1", "XXXXXX")
        _wait(app, lambda: len(c.instances) == 2 and all(i.name for i in c.instances))
        c.setCurrent(next(n for n, i in enumerate(c.instances) if i.name == "lake3"))
        assert store.value("ui/current") == "lake3"
        for f in feeds:
            f.stop()
        feeds = _feeds(["XXXXXX", "XXXXXX"], ["lake3", "lake2"])  # lake3 now on the first port
        monkeypatch.setattr(backend, "SCAN_PORTS", [f.port for f in feeds])
        c._scan(c._servers[0])
        first = feeds[0].port
        _wait(app, lambda: c.current is not None and c.current.name == "lake3"
              and c.current.state == "live" and url_parts(c.current.feed[0])[1] == first, 15)
        assert c.current.name == "lake3" and url_parts(c.current.feed[0])[1] == first
        for i in c.instances:
            i.close()
    finally:
        for f in feeds:
            f.stop()


def test_hidden_window_queue_is_capped() -> None:
    # a minimised window draws nothing; rows keep arriving and must not pile up without bound
    from backend import PENDING_MAX, SPECTRUM_HEADER, Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    inst = Instance("ws://127.0.0.1:9/feed")
    for k in range(PENDING_MAX * 5):
        inst._on_binary(SPECTRUM_HEADER.pack(1, 1_000.0 + k / 60, 7.0e6, 50.0, 0.25, 4) + bytes(4))
    assert len(inst.pending) == PENDING_MAX


def test_spot_detail_uses_the_spot_not_later_noise() -> None:
    # after a station stops its bin decodes noise; its spot must still show the spot's numbers
    from backend import Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    inst = Instance("ws://127.0.0.1:9/feed")
    heard = ((100.0, "CQ CQ DE WX7V WX7V K", 18.5, 32.6), (160.0, "E E", 31.2, 13.0))
    for utc, text, wpm, snr in heard:
        inst._add_window({"type": "window", "khz": 7046.99, "text": text, "wpm": wpm, "snr": snr,
                          "cost": 0.3, "utc": utc})
    inst.spots["WX7V"] = {"type": "spot", "khz": 7046.99, "call": "WX7V", "snr": 27.4, "wpm": 19,
                          "utc": 120.0}
    inst.select(7047.0, "WX7V")
    d = inst.property("detail")
    assert (d["spot_khz"], d["wpm"], d["snr"]) == (7046.99, 19, 27.4)
    del inst.spots["WX7V"]  # heard, not spotted: the newest window that copied the call
    d = inst.property("detail")
    assert (d["spot_khz"], d["wpm"], d["snr"]) == (None, 18.5, 32.6)


@pytest.mark.parametrize(
    ("query", "khz"),
    [("7030", 7030.0), ("7.030", 7030.0), ("14025.5", 14025.5), ("INF", None), ("NAN", None),
     ("1E3", None), ("7_030", None), ("7.0.3", None), (".", None)],
)
def test_search_takes_only_digits_as_a_frequency(query: str, khz: float | None) -> None:
    # as in SparkGap Web: INF, NAN or 1E3 are not frequencies
    from backend import Instance
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    m = Instance("ws://127.0.0.1:9/feed").search(query)
    assert (m[0]["khz"] if m and not m[0]["call"] else None) == khz


def test_no_answer_is_said_plainly() -> None:
    from backend import Instance
    from PySide6.QtCore import QCoreApplication, QTimer

    app = QCoreApplication.instance() or QCoreApplication([])
    inst = Instance("ws://127.0.0.1:9/feed")
    inst.open()
    QTimer.singleShot(1500, app.quit)
    app.exec()
    assert inst.error == "no answer"
    inst.close()


def _trace_rows(span_khz: float, left: float, right: float) -> list[int]:
    """Pixel rows drawn above the background for one CW carrier at bin 100 (46.875 Hz bins, as at
    96 kHz), its neighbours at the given dB, in a 600 px panel showing span_khz round it."""
    from types import SimpleNamespace

    import numpy as np
    from backend import DB_FLOOR, Waterfall

    inst: Any = SimpleNamespace(hz_per_bin=46.875, first_hz=0.0, n_bins=200)
    centre_khz = (100 + 0.5) * 46.875 / 1000
    half = span_khz / 2
    w: Any = SimpleNamespace(_lo=centre_khz - half, _hi=centre_khz + half, _map=None)
    Waterfall._build_map(w, inst, 600)
    v = np.zeros((200, 1), dtype=np.float32)
    v[99, 0], v[100, 0], v[101, 0] = left, 30.0, right
    return [int(y) for y in np.nonzero(Waterfall._peaks(w, v)[:, 0] > DB_FLOOR)[0]]


def test_a_cw_trace_keeps_its_width_when_zoomed_in() -> None:
    wide, narrow = _trace_rows(5.0, 24.0, 24.0), _trace_rows(2.5, 24.0, 24.0)
    from backend import PEAK_MAX_PX, PEAK_MIN_PX, peak_px

    assert len(wide) == peak_px(600 / (5_000 / 46.875)) and len(narrow) == PEAK_MAX_PX
    assert PEAK_MIN_PX <= len(wide) <= len(narrow) <= PEAK_MAX_PX  # never shrinks, never balloons
    assert abs((wide[0] + wide[-1]) / 2 - 299.5) <= 1  # at the carrier, the panel's middle


def test_a_cw_trace_sits_at_its_frequency_not_its_bins() -> None:
    # a stronger upper neighbour puts the carrier above the bin centre: higher, so nearer the top
    rows = _trace_rows(5.0, 20.0, 28.0)
    assert (rows[0] + rows[-1]) / 2 < 299.5 - 1
