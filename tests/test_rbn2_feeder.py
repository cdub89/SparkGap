"""rbn2_feeder: the auth library's contract (through a fake, never a real library), fail closed,
posts on the upload tick, and the sign-in."""

import ctypes
import queue
import threading
import zlib
from typing import Any

import pytest

import rbn2_feeder as f


def fake_hash(tag: str, text: str) -> str:
    return f"{zlib.crc32((tag + text).encode()):08x}"


class FakeFn:
    """A library function: argtypes and restype settable, like a ctypes function."""

    def __init__(self, impl: Any) -> None:
        self.impl, self.argtypes, self.restype = impl, None, None

    def __call__(self, *args: Any) -> int:
        return self.impl(*args)


class FakeLib:
    def __init__(
        self,
        ret: int = 0,
        refuse: str = "",
        version: bytes = b"rbn2auth/1.2",
        bad_hash: bytes | None = None,
    ) -> None:
        def ver(buf: Any, _n: int) -> int:
            buf.value = version
            return ret

        def hashed(tag: str) -> Any:
            def h(text: bytes, out: Any, _n: int) -> int:
                if not text or text.decode() == refuse:
                    return -1
                out.value = (
                    bad_hash if bad_hash is not None else fake_hash(tag, text.decode()).encode()
                )
                return 0

            return h

        def spot(calls: Any, n: int, out: Any, _n: int) -> int:
            got = [calls[i].decode() for i in range(n)]
            if not got or refuse in got or "" in got:
                return -1
            out.value = bad_hash if bad_hash is not None else fake_hash("h", "|".join(got)).encode()
            return 0

        self.rbn2_auth_version = FakeFn(ver)
        self.rbn2_auth_short_hash = FakeFn(hashed("s"))
        self.rbn2_auth_spot_hash = FakeFn(spot)


def _auth(**kw: Any) -> f.Auth:
    return f.Auth("librbn2_auth.so", loader=lambda _p: FakeLib(**kw))


def _session(auth: f.Auth | None, dry_run: bool = False) -> f.Session:
    return f.Session(
        url="http://rbn.invalid/rx/6/",
        call="N0CALL-1",
        name="SparkGap",
        grid="FN31pr",
        qth="",
        validation="Normal",
        port=7300,
        bands=[(7000.0, 7096.0)],
        auth=auth,
        version="SparkGap/abc1234",
        fingerprint="f" * 32,
        dry_run=dry_run,
    )


SPOT = {"freq": 7027.64, "call": "JA0FVU", "cq_flag": "CQ", "snr": 25, "wpm": 30, "mode": "CW"}


def test_the_library_is_set_up_and_reports_its_version() -> None:
    auth = _auth()
    assert auth.version == "rbn2auth/1.2"
    assert auth._short.argtypes == [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    assert auth._spot.argtypes == [
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]


def test_another_version_is_refused() -> None:
    with pytest.raises(f.AuthError, match="version 'rbn2auth/7a2eb2c'"):
        _auth(version=b"rbn2auth/7a2eb2c", ret=1)  # the one-call library
    with pytest.raises(f.AuthError, match="version 'rbn2auth/1.3'"):
        _auth(version=b"rbn2auth/1.3")


def test_a_failed_version_call_is_refused() -> None:
    with pytest.raises(f.AuthError, match="no version"):
        _auth(ret=-1)


def test_a_missing_library_is_refused() -> None:
    def missing(path: str) -> Any:
        raise OSError(f"{path}: cannot open shared object file")

    with pytest.raises(f.AuthError, match="cannot load"):
        f.Auth("/nowhere/librbn2_auth.so", loader=missing)


def test_a_library_without_a_function_is_refused() -> None:
    lib = FakeLib()
    del lib.rbn2_auth_spot_hash
    with pytest.raises(f.AuthError, match="cannot load"):
        f.Auth("librbn2_auth.so", loader=lambda _p: lib)


@pytest.mark.parametrize("version", [b"", b"\xff\xfe", b"rbn2\x01auth"])
def test_an_unusable_version_string_is_refused(version: bytes) -> None:
    with pytest.raises(f.AuthError, match="version"):
        _auth(version=version)


@pytest.mark.parametrize("bad", [b"", b"abc", b"zzzzzzzz", b"0123456789", b"\xff" * 8])
def test_a_malformed_hash_is_a_refusal(bad: bytes) -> None:
    sess = _session(_auth(bad_hash=bad))
    assert sess.spots_body([SPOT], 0.0) is None
    assert sess.id_body() is None


def test_without_a_library_or_a_version_nothing_is_posted() -> None:
    assert f.not_ready(None, "SparkGap/abc1234", dry_run=False)
    assert f.not_ready(_auth(), None, dry_run=False)
    assert f.not_ready(_auth(), "SparkGap/abc1234", dry_run=False) is None
    assert f.not_ready(None, None, dry_run=True) is None


def test_a_session_without_a_library_may_only_dry_run() -> None:
    with pytest.raises(f.AuthError):
        _session(None)
    assert _session(None, dry_run=True).agg == "rbn2auth/none"


def test_one_spot_is_hashed_as_a_list_of_one() -> None:
    sess = _session(_auth())
    body = sess.spots_body([SPOT], 1_761_000_000.0)
    assert body is not None
    assert body["h"] == fake_hash("h", "JA0FVU")
    assert body["agg"] == "rbn2auth/1.2" and body["e"] == "N0CALL-1"
    assert body["s"] == [["7027.64", "JA0FVU", "1", "25", "30", "dB", "1", "CQ", "D:"]]


def test_one_hash_covers_the_post_s_calls_in_row_order() -> None:
    sess = _session(_auth())
    body = sess.spots_body([SPOT, {**SPOT, "call": "K1ABC", "freq": 7031.0}], 0.0)
    assert body is not None and body["h"] == fake_hash("h", "JA0FVU|K1ABC")
    assert [r[:2] for r in body["s"]] == [["7027.64", "JA0FVU"], ["7031.00", "K1ABC"]]
    assert sess.auth is not None and sess.auth.spot_hash([]) is None


def test_a_refused_call_drops_the_spot() -> None:
    assert _session(_auth(refuse="JA0FVU")).spots_body([SPOT], 0.0) is None
    both = [SPOT, {**SPOT, "call": "K1ABC"}]
    assert _session(_auth(refuse="K1ABC")).spots_body(both, 0.0) is None


def test_the_sign_in_names_sparkgap_and_its_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f, "ntp_synced", lambda: True)
    sess = _session(_auth())
    body = sess.id_body()
    assert body is not None
    sign_in = "SparkGap/abc1234 is operated by SparkGap, N0CALL-1"
    assert body["skimSignIn"] == sign_in and body["shortHash"] == fake_hash("s", sign_in)
    assert body["skimVersion"] == "SparkGap/abc1234" and body["aggVersion"] == "rbn2auth/1.2"
    assert body["justCQ"] == "1" and body["ClockNtp"] is True
    assert body["bandLimits"] == body["mIXEDBandLimits"] == "7000.0-7096.0"
    assert body["cWBandLimits"] == ""
    spot = sess.spots_body([SPOT], 0.0)
    assert spot is not None and spot["nTP"] is True  # as the last id.php reported


def test_a_refused_sign_in_sends_no_id_and_no_spots(monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _session(_auth(refuse="SparkGap/abc1234 is operated by SparkGap, N0CALL-1"))
    posts: list[str] = []
    monkeypatch.setattr(sess, "post", lambda path, body: posts.append(path) or {})
    assert sess.post_id() is False and posts == [] and not sess.registered.is_set()


def test_a_failed_id_stops_spots_until_one_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _session(_auth())
    replies: list[dict[str, Any] | None] = [{}, None, {}]
    monkeypatch.setattr(sess, "post", lambda path, body: replies.pop(0))
    assert sess.post_id() and sess.registered.is_set()
    assert not sess.post_id() and not sess.registered.is_set()
    assert sess.post_id() and sess.registered.is_set()


def _run_upload(
    sess: f.Session, calls: list[str], blacklist: set[str], posts: int
) -> list[dict[str, Any]]:
    """Queue the calls, run the loop on a fast tick until it has made `posts` posts (or 1 s)."""
    sent: list[dict[str, Any]] = []
    spots: queue.Queue[dict[str, Any]] = queue.Queue()
    for call in calls:
        spots.put({**SPOT, "call": call})
    shutdown = threading.Event()

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        if len(sent) >= posts:
            shutdown.set()
        return {}

    sess.post = post  # type: ignore[method-assign]
    sess.upload_s = 0.01
    t = threading.Thread(target=f.upload_loop, args=(sess, spots, blacklist, shutdown, lambda: 0.0))
    t.start()
    t.join(1.0)
    shutdown.set()
    t.join()
    return sent


def test_a_tick_s_spots_go_in_one_post() -> None:
    sess = _session(_auth())
    sess.registered.set()
    sent = _run_upload(sess, ["JA0FVU", "N0CALL", "K1ABC"], {"N0CALL"}, posts=1)  # blacklisted
    assert [[r[1] for r in b["s"]] for b in sent] == [["JA0FVU", "K1ABC"]]


def test_every_spot_queued_goes_in_one_post() -> None:
    sess = _session(_auth())
    sess.registered.set()
    calls = [f"K{i}AA" for i in range(70)]
    sent = _run_upload(sess, calls, set(), posts=1)
    assert len(sent) == 1 and [r[1] for r in sent[0]["s"]] == calls
    assert sent[0]["h"] == fake_hash("h", "|".join(calls))


def test_the_tick_follows_the_server_s_upload_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _session(_auth())
    replies: list[dict[str, Any]] = [
        {"serverUploadInterval": 20},
        {"serverUploadInterval": "x"},
        {},
    ]
    monkeypatch.setattr(sess, "post", lambda path, body: replies.pop(0))
    assert sess.upload_s == f.UPLOAD_S
    sess.post_id()
    assert sess.upload_s == 20.0
    sess.post_id()
    sess.post_id()
    assert sess.upload_s == 20.0


def test_no_spot_goes_out_before_the_sign_in_succeeds() -> None:
    sess = _session(_auth())
    assert _run_upload(sess, ["JA0FVU", "K1ABC"], set(), posts=1) == []


def test_a_reply_that_is_not_json_text_counts_as_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        def __enter__(self) -> "Resp":
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def read(self) -> bytes:
            return b"\xff\xfe not json"

    monkeypatch.setattr(f.urlreq, "urlopen", lambda req, timeout: Resp())
    assert _session(_auth()).post("s.php", {"t": "s"}) == {}


SPOT_LINE = (
    b"DX de WX7V-5-#:  14025.00  K7WP         CW   21 dB 27 WPM  CQ"
    b"                  SG  1945Z\a\r\n"
)


class FakeSock:
    """recv replies in order: bytes, or an exception to raise."""

    def __init__(self, replies: list[Any], shutdown: threading.Event) -> None:
        self.replies, self.shutdown, self.sent = replies, shutdown, b""

    def __enter__(self) -> "FakeSock":
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, n: int) -> bytes:
        if not self.replies:
            self.shutdown.set()
            return b""
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _read(replies: list[Any]) -> tuple[list[dict[str, Any]], int]:
    shutdown = threading.Event()
    spots: queue.Queue[dict[str, Any]] = queue.Queue()
    sock = FakeSock(replies, shutdown)
    connects = []

    def connect(addr: Any, timeout: float) -> FakeSock:
        connects.append(addr)
        return sock

    f.read_local("127.0.0.1", 7300, "WX7V-5", spots, shutdown, connect)  # type: ignore[arg-type]
    assert sock.sent == b"WX7V-5\n"
    return [spots.get() for _ in range(spots.qsize())], len(connects)


def test_a_quiet_minute_keeps_the_connection() -> None:
    got, connects = _read([TimeoutError(), TimeoutError(), SPOT_LINE])
    assert [s["call"] for s in got] == ["K7WP"]
    assert connects == 1


def test_a_spot_split_across_reads_is_put_together() -> None:
    got, _ = _read([SPOT_LINE[:20], SPOT_LINE[20:]])
    assert [s["call"] for s in got] == ["K7WP"]


def test_eof_reconnects_and_reads_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f, "RECONNECT_S", 0.0)
    shutdown = threading.Event()
    spots: queue.Queue[dict[str, Any]] = queue.Queue()
    socks = [FakeSock([b""], threading.Event()), FakeSock([SPOT_LINE], shutdown)]
    f.read_local("127.0.0.1", 7300, "WX7V-5", spots, shutdown, lambda a, timeout: socks.pop(0))  # type: ignore[arg-type]
    assert not socks
    assert spots.get_nowait()["call"] == "K7WP"


def test_a_failed_thread_stops_the_feeder() -> None:
    shutdown, failed = threading.Event(), threading.Event()

    def boom() -> None:
        raise ValueError("bad reply")

    f.worker(boom, shutdown, failed)()
    assert failed.is_set() and shutdown.is_set()


def test_a_thread_that_ends_unasked_stops_the_feeder() -> None:
    shutdown, failed = threading.Event(), threading.Event()
    f.worker(lambda: None, shutdown, failed)()
    assert failed.is_set() and shutdown.is_set()
    shutdown, failed = threading.Event(), threading.Event()
    shutdown.set()
    f.worker(lambda: None, shutdown, failed)()  # asked to stop: not a failure
    assert not failed.is_set()


def test_a_broken_http_reply_is_a_failed_post(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(req: Any, timeout: float) -> Any:
        raise f.http.client.IncompleteRead(b"")

    monkeypatch.setattr(f.urlreq, "urlopen", urlopen)
    assert _session(_auth()).post("s.php", {"t": "s"}) is None


def test_a_dry_run_logs_no_hash_fingerprint_or_sign_in(caplog: pytest.LogCaptureFixture) -> None:
    sess = _session(_auth(), dry_run=True)
    caplog.set_level("INFO")
    sess.post_id()
    body = sess.spots_body([SPOT], 0.0)
    assert body is not None
    sess.post("s.php", body)
    logged = caplog.text
    assert sess.auth is not None
    secrets = (
        "f" * 32,
        sess.sign_in(),
        sess.auth.short_hash(sess.sign_in()),
        sess.auth.spot_hash(["JA0FVU"]),
    )
    for secret in secrets:
        assert secret and secret not in logged
    assert '"fingerPrint":"<hidden>"' in logged and '"h":"<hidden>"' in logged
    assert '"skimSignIn":"<hidden>"' in logged
