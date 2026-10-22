"""sparkgap_agent.py: the seat over a WebSocket, a copy's feed passed through, the web page."""

import json
import socket
import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import sparkgap_agent
from feed_server import OP_CLOSE, OP_TEXT, FeedServer
from seat import Seat
from sparkgap_agent import AGENT_PROTOCOL, Agent, load_settings
from tests.test_feed_server import WS
from tests.test_seat import seat  # noqa: F401  (the fixture: a temporary root with stand-ins)

CODE = "ACDEFH"


@pytest.fixture
def agent(seat: Seat) -> Iterator[Agent]:  # noqa: F811
    a = Agent(seat.root, {"bind": "127.0.0.1", "port": 0, "join_code": CODE})
    threading.Thread(target=lambda: _run(a), daemon=True).start()
    assert a.ready.wait(5)
    yield a


def _run(a: Agent) -> None:
    import asyncio

    asyncio.run(a.serve())


def _ws(a: Agent, code: str = CODE, path: str = "/agent", proto: str = AGENT_PROTOCOL) -> WS:
    return WS(a.port, path=path, protocols=f"{proto}, sparkgap.code.{code}")


def _result(ws: WS, rid: int) -> dict:
    while True:
        msg = ws.json("agent_result")
        if msg["id"] == rid:
            return msg


def test_a_wrong_code_is_refused(agent: Agent) -> None:
    ws = _ws(agent, code="XXXXXX")
    op, data = ws.frame()
    assert op == OP_CLOSE and int.from_bytes(data[:2], "big") == 4401


def test_hello_then_the_seat_without_copy_codes(agent: Agent) -> None:
    ws = _ws(agent)
    hello = ws.json("hello")
    assert hello["agent"] is True and ws.headers.count(AGENT_PROTOCOL) == 1
    st = ws.json("seat")
    assert [i["name"] for i in st["instances"]] == ["dallas1", "dallas2"]
    assert all("feed_code" not in i for i in st["instances"])


def test_start_through_the_agent_and_busy_while_it_runs(agent: Agent) -> None:
    ws = _ws(agent)
    msg = {"type": "agent", "id": 1, "cmd": "start", "name": "dallas1"}
    ws.send(OP_TEXT, json.dumps(msg).encode())
    ws.send(OP_TEXT, json.dumps({**msg, "id": 2}).encode())
    assert "busy" in _result(ws, 2)["error"]
    assert _result(ws, 1)["ok"] and agent.seat.pid("dallas1")


def test_a_setting_off_the_list_is_refused(agent: Agent) -> None:
    ws = _ws(agent)
    msg = {"type": "agent", "id": 3, "cmd": "set", "name": "dallas1", "settings": {"callsign": "X"}}
    ws.send(OP_TEXT, json.dumps(msg).encode())
    r = _result(ws, 3)
    assert not r["ok"] and "cannot be changed" in r["error"]


def test_a_copys_feed_is_passed_through_with_its_own_code(agent: Agent, seat: Seat) -> None:  # noqa: F811
    feed = FeedServer({"bind": "127.0.0.1", "port": 0, "join_code": "ABCDEF"}, {"bands": []})
    feed.start()
    try:
        copy = seat.root / "code" / "sparkgap.py"
        copy.write_text(copy.read_text().replace("7321", str(feed.port)))
        seat.start("dallas1")
        ws = _ws(agent, path="/feed/dallas1", proto="sparkgap.v1")
        assert "101" in ws.headers.splitlines()[0]
        assert ws.json("hello")["v"] == 1 and ws.json("snapshot")
    finally:
        feed.stop()


def test_a_stopped_copy_has_no_feed(agent: Agent) -> None:
    ws = _ws(agent, path="/feed/dallas2", proto="sparkgap.v1")
    op, data = ws.frame()
    assert op == OP_CLOSE and b"no feed running" in data


def test_sparkgap_web_is_served(agent: Agent) -> None:
    with socket.create_connection(("127.0.0.1", agent.port), timeout=3) as s:
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        time.sleep(0.2)
        head = s.recv(200)
    assert head.startswith(b"HTTP/1.1 200") and b"text/html" in head


def test_first_run_writes_an_owner_only_settings_file(tmp_path: Path) -> None:
    cfg = load_settings(tmp_path)
    path = tmp_path / "agent.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(cfg["join_code"]) == 6
    assert cfg["port"] == sparkgap_agent.DEFAULT_PORT == 7320
    assert load_settings(tmp_path) == cfg


def test_a_client_that_vanishes_is_not_an_error(
    agent: Agent, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _ws(agent)
    ws.json("seat")
    ws.s.close()  # no close frame: a closed tab or a dropped link
    time.sleep(0.3)
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]
    assert not agent.clients


def test_half_open_requests_beyond_the_cap_are_closed(
    agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sparkgap_agent, "MAX_PENDING", 2)
    idle = [socket.create_connection(("127.0.0.1", agent.port)) for _ in range(2)]
    time.sleep(0.2)
    with socket.create_connection(("127.0.0.1", agent.port), timeout=2) as s:
        assert s.recv(1) == b""  # closed before any request is read
    for s in idle:
        s.close()


def test_the_copy_side_is_closed_when_the_browser_leaves(agent: Agent) -> None:
    """A copy that never sends: the agent must still let go of it once the browser has gone."""
    srv = socket.create_server(("127.0.0.1", 0))
    upstream_closed = threading.Event()

    def copy() -> None:
        conn, _ = srv.accept()
        raw = b""
        while b"\r\n\r\n" not in raw:
            raw += conn.recv(1024)
        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        )
        conn.settimeout(5)
        try:
            if conn.recv(1024) == b"":
                upstream_closed.set()
        except OSError:
            pass

    threading.Thread(target=copy, daemon=True).start()
    agent._feed_target = lambda name: ("127.0.0.1", srv.getsockname()[1], None)  # type: ignore[method-assign]
    ws = _ws(agent, path="/feed/dallas1", proto="sparkgap.v1")
    assert "101" in ws.headers.splitlines()[0]
    ws.s.close()
    assert upstream_closed.wait(3)
    srv.close()


def test_a_copy_port_search_of_the_agent_is_not_a_wrong_code(agent: Agent) -> None:
    for _ in range(6):  # a web page searching 7320 for copies, with a copy's code
        _ws(agent, code="XXXXXX", path="/feed", proto="sparkgap.v1").s.close()
    ws = _ws(agent)
    assert ws.json("hello")["agent"] is True  # not locked out
