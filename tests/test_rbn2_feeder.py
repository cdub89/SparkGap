"""rbn2_feeder: the auth library's contract (through a fake, never a real library), fail closed,
one spot per post, and the sign-in."""

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
        interface: int = 1,
        refuse: str = "",
        version: bytes = b"rbn2auth/test",
        bad_hash: bytes | None = None,
    ) -> None:
        def ver(buf: Any, _n: int) -> int:
            buf.value = version
            return interface

        def hashed(tag: str) -> Any:
            def h(text: bytes, out: Any, _n: int) -> int:
                if not text or text.decode() == refuse:
                    return -1
                out.value = (
                    bad_hash if bad_hash is not None else fake_hash(tag, text.decode()).encode()
                )
                return 0

            return h

        self.rbn2_auth_version = FakeFn(ver)
        self.rbn2_auth_short_hash = FakeFn(hashed("s"))
        self.rbn2_auth_spot_hash = FakeFn(hashed("h"))


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
    assert auth.version == "rbn2auth/test"
    assert auth._spot.argtypes == [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]


def test_another_interface_is_refused() -> None:
    with pytest.raises(f.AuthError, match="interface 2"):
        _auth(interface=2)


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
    assert sess.spot_body(SPOT, 0.0) is None
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


def test_a_spot_is_one_post_hashed_from_its_call() -> None:
    body = _session(_auth()).spot_body(SPOT, 1_761_000_000.0)
    assert body is not None
    assert body["h"] == fake_hash("h", "JA0FVU")
    assert body["agg"] == "rbn2auth/test" and body["e"] == "N0CALL-1"
    assert body["s"] == [["7027.64", "JA0FVU", "1", "25", "30", "dB", "1", "CQ", "D:"]]


def test_a_refused_call_drops_the_spot() -> None:
    assert _session(_auth(refuse="JA0FVU")).spot_body(SPOT, 0.0) is None


def test_the_sign_in_names_sparkgap_and_its_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f, "ntp_synced", lambda: True)
    sess = _session(_auth())
    body = sess.id_body()
    assert body is not None
    sign_in = "SparkGap/abc1234 is operated by SparkGap, N0CALL-1"
    assert body["skimSignIn"] == sign_in and body["shortHash"] == fake_hash("s", sign_in)
    assert body["skimVersion"] == "SparkGap/abc1234" and body["aggVersion"] == "rbn2auth/test"
    assert body["justCQ"] == "1" and body["ClockNtp"] is True
    assert body["bandLimits"] == body["mIXEDBandLimits"] == "7000.0-7096.0"
    assert body["cWBandLimits"] == ""
    spot = sess.spot_body(SPOT, 0.0)
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


def _run_upload(sess: f.Session, calls: list[str], blacklist: set[str]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    spots: queue.Queue[dict[str, Any]] = queue.Queue()
    for call in calls:
        spots.put({**SPOT, "call": call})
    shutdown = threading.Event()

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return {}

    sess.post = post  # type: ignore[method-assign]
    t = threading.Thread(target=f.upload_loop, args=(sess, spots, blacklist, shutdown, lambda: 0.0))
    t.start()
    while not spots.empty():  # the loop finishes a spot it has taken before checking shutdown
        shutdown.wait(0.01)
    shutdown.set()
    t.join()
    return sent


def test_each_spot_goes_out_on_its_own() -> None:
    sess = _session(_auth())
    sess.registered.set()
    sent = _run_upload(sess, ["JA0FVU", "N0CALL", "K1ABC"], {"N0CALL"})  # N0CALL is blacklisted
    assert [b["s"][0][1] for b in sent] == ["JA0FVU", "K1ABC"]
    assert all(len(b["s"]) == 1 for b in sent)


def test_no_spot_goes_out_before_the_sign_in_succeeds() -> None:
    sess = _session(_auth())
    assert _run_upload(sess, ["JA0FVU", "K1ABC"], set()) == []


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
