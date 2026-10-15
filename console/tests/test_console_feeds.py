"""Console server list: URL handling, checks, and the list kept in QSettings."""

import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtWebSockets")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import (  # noqa: E402
    SPECTRUM_HEADER,
    check_feed,
    feed_url,
    load_feeds,
    merge_feeds,
    save_feeds,
    url_parts,
)
from PySide6.QtCore import QSettings  # noqa: E402
from sparkgap_console import parse  # noqa: E402


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
