"""Console server list: URL handling, checks, and the list kept in QSettings."""

import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtWebSockets")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import (
    SPECTRUM_HEADER,
    check_feed,
    feed_url,
    load_feeds,
    merge_feeds,
    open_request,
    save_feeds,
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
    ("address", "port", "ok"),
    [
        ("127.0.0.1", 7320, True),
        ("127.0.0.1", 7321, True),
        ("", 7320, False),
        ("ws://127.0.0.1", 7320, False),
        ("127.0.0.1:7321", 7320, False),
        ("local host", 7320, False),
        ("127.0.0.1", 0, False),
        ("127.0.0.1", 70_000, False),
    ],
)
def test_check_feed(address: str, port: int, ok: bool) -> None:
    assert (check_feed(address, port) == "") is ok


@pytest.mark.parametrize(
    ("code", "ok"),
    [("", True), ("XXXXXX", True), ("xxx-xxx", True), (" xxx xxx ", True),
     ("XXXXX", False), ("XXXXXXX", False), ("XXXXXO", False), ("XXXXX1", False)],
)
def test_check_join_code(code: str, ok: bool) -> None:
    assert (check_feed("127.0.0.1", 7320, code) == "") is ok


def test_save_load_round_trip(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
    feeds = [("ws://127.0.0.1:7321/feed", "tok1"), ("ws://127.0.0.1:7320/feed", "")]
    save_feeds(store, feeds)
    assert load_feeds(QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)) == feeds
    save_feeds(store, feeds[:1])  # a shorter list leaves no stale rows behind
    assert load_feeds(store) == feeds[:1]


def test_merge_keeps_order_and_takes_newer_token() -> None:
    saved = [("ws://a:7320/feed", "old"), ("ws://b:7320/feed", "x")]
    extra = [("ws://a:7320/feed", "new"), ("ws://c:7320/feed", "y")]
    assert merge_feeds(saved, extra) == [
        ("ws://a:7320/feed", "new"),
        ("ws://b:7320/feed", "x"),
        ("ws://c:7320/feed", "y"),
    ]


def test_parse_normalises_to_saved_form() -> None:
    assert parse(["127.0.0.1:7321#xxx-xxx"]) == [("ws://127.0.0.1:7321/feed", "XXXXXX")]
    assert parse(["ws://127.0.0.1/feed"]) == [("ws://127.0.0.1:7320/feed", "")]
    assert parse([]) == []


def test_console_add_edit_remove(tmp_path: Path) -> None:
    from backend import Console
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841 - sockets need one
    store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
    c = Console([], store)
    assert c.saveFeed(-1, "local host", 7320, "") != ""
    assert c.saveFeed(-1, "127.0.0.1", 9, "xxx-xxx") == ""
    assert load_feeds(store) == [("ws://127.0.0.1:9/feed", "XXXXXX")]  # kept normalised
    assert c.saveFeed(-1, "127.0.0.1", 9, "") == "already in the list"
    assert c.saveFeed(0, "127.0.0.1", 9, "YYYYYY") == ""  # editing a row may keep its own address
    assert c.saveFeed(-1, "localhost", 10, "") == "" and c.currentIndex == 1
    c.removeFeed(0)
    assert load_feeds(store) == [("ws://localhost:10/feed", "")] and c.currentIndex == 0
    c.removeFeed(0)
    assert load_feeds(store) == [] and c.current is None


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
    c: Any = Console([("ws://127.0.0.1:9/feed", "")], store)
    server = c.current
    c.setDemo(True)
    assert c.demo and c.current.demo and c.current is not server
    assert server in c.instances and not server.closed  # still connected behind the demo
    c._save()
    assert load_feeds(store) == [("ws://127.0.0.1:9/feed", "")]  # demo is never saved
    c.setDemo(False)
    assert not c.demo and c.current is server and len(c.instances) == 1


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
    from backend import SCAN_PORTS, scan_report

    add, update, text = scan_report({7320: "ok", 7321: "ok", 7322: "code", 7323: "none"}, [7321])
    assert (add, update) == ([7320], [])
    assert text == "Added 7320. Already in the list: 7321. Different join code on 7322."
    assert scan_report({p: "none" for p in SCAN_PORTS}, [])[2].startswith("No SparkGap answered")
    add, update, text = scan_report({7320: "ok", 7321: "ok"}, [7320, 7321], stale=[7321])
    assert (add, update) == ([], [7321])
    assert text == "Join code updated on 7321. Already in the list: 7320."


def test_find_feeds_adds_every_instance_with_the_code(tmp_path: Path, monkeypatch: Any) -> None:
    # one code finds each SparkGap on the address; a different code is reported, once, never retried
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import backend
    from PySide6.QtCore import QCoreApplication, QTimer

    import feed_server

    app = QCoreApplication.instance() or QCoreApplication([])
    feeds = [feed_server.FeedServer({"bind": "127.0.0.1", "port": 0, "join_code": c}, {})
             for c in ("XXXXXX", "XXXXXX", "YYYYYY")]
    for f in feeds:
        f.start()
        f.started.wait(5)
    try:
        used = [f.port for f in feeds]
        closed = next(p for p in range(used[0] + 1, used[0] + 10) if p not in used)
        monkeypatch.setattr(backend, "SCAN_PORTS", [f.port for f in feeds] + [closed])
        store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
        c = backend.Console([], store)
        done: list[tuple[bool, str]] = []
        c.scanDone.connect(lambda added, text: (done.append((added, text)), app.quit()))
        assert c.findFeeds(-1, "127.0.0.1", "xxx-xxx") == ""
        assert c.findFeeds(-1, "127.0.0.1", "XXXXXX") == "already searching"
        QTimer.singleShot(8000, app.quit)
        app.exec()
        ok = sorted(f.port for f in feeds[:2])
        assert done and done[0][0]
        assert sorted(url_parts(u)[1] for u, _ in load_feeds(store)) == ok
        assert all(t == "XXXXXX" for _, t in load_feeds(store))
        assert f"Different join code on {feeds[2].port}." in done[0][1]
        assert len(feeds[2].misses["127.0.0.1"]) == 1
        for i in c._instances:
            i.close()
    finally:
        for f in feeds:
            f.stop()


def test_edit_without_port_finds_all_and_replaces_stale_codes(tmp_path: Path,
                                                              monkeypatch: Any) -> None:
    # a SparkGap restarted with a new code: edit one entry, clear its port, give the new code
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import backend
    from PySide6.QtCore import QCoreApplication, QTimer

    import feed_server

    app = QCoreApplication.instance() or QCoreApplication([])
    feeds = [feed_server.FeedServer({"bind": "127.0.0.1", "port": 0, "join_code": "XXXXXX"}, {})
             for _ in range(2)]
    for f in feeds:
        f.start()
        f.started.wait(5)
    try:
        monkeypatch.setattr(backend, "SCAN_PORTS", [f.port for f in feeds])
        store = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
        c = backend.Console([], store)
        for f in feeds:
            assert c.saveFeed(-1, "127.0.0.1", f.port, "YYYYYY") == ""  # the old code
        done: list[tuple[bool, str]] = []
        c.scanDone.connect(lambda added, text: (done.append((added, text)), app.quit()))
        assert c.findFeeds(0, "127.0.0.1", "XXXXXX") == ""
        QTimer.singleShot(8000, app.quit)
        app.exec()
        assert done and done[0][0]
        saved = load_feeds(store)
        assert sorted(url_parts(u)[1] for u, _ in saved) == sorted(f.port for f in feeds)
        assert all(t == "XXXXXX" for _, t in saved)
        assert f"Join code updated on {feeds[1].port}." in done[0][1]
        for i in c._instances:
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
