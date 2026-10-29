#!/usr/bin/env python3
"""rbn2_feeder.py: SparkGap's RBN feeder, authenticated by the private librbn2_auth library.

Reads SparkGap's local cluster telnet like rbn_feeder.py (whose spot parsing, reader and band
probe it reuses) and posts to RBN's id.php and s.php. The two hash fields come from the
library named by --auth-lib, which is handed out separately and never kept in this repo.
Without a library of the expected interface the feeder does not post (--dry-run aside).

Differences from rbn_feeder.py: one spot per post, sent as it arrives; heartbeat every 300 s;
SparkGap identifies itself as SparkGap/<short commit>, with the library's version string as
the Aggregator version.

usage: rbn2_feeder.py --call N0CALL-1 --grid FN31pr --auth-lib PATH [--dry-run]
"""

import argparse
import ctypes
import hashlib
import json
import logging
import queue
import socket
import string
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlreq

import rbn_feeder as base

log = logging.getLogger("rbn2_feeder")

AUTH_INTERFACE = 1  # the librbn2_auth interface this feeder is written for
VERSION_LEN = 64
HASH_LEN = 16
HASH_CHARS = 8
HEARTBEAT_S = 300.0
ID_RETRY_S = 60.0  # after a failed id.php; spots wait until it succeeds
HTTP_TIMEOUT_S = 15
QUEUE_POLL_S = 1.0
MODE_CODE = {"CW": "1", "RTTY": "11"}  # Aggregator 6.7's mode table
CQ_CODE = {"CQ": "1", "DX": "2", "BCN": "3", "BEACON": "3"}


class AuthError(RuntimeError):
    """The auth library is missing or not the interface this feeder expects."""


class Auth:
    """librbn2_auth through ctypes: its version string and the two hashes."""

    def __init__(self, path: str, loader: Callable[[str], Any] = ctypes.CDLL) -> None:
        try:
            lib = loader(path)
            ver, short, spot = (
                lib.rbn2_auth_version,
                lib.rbn2_auth_short_hash,
                lib.rbn2_auth_spot_hash,
            )
        except (OSError, AttributeError) as e:
            raise AuthError(f"cannot load {path}: {e}") from e
        ver.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        ver.restype = ctypes.c_int
        for fn in (short, spot):
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(VERSION_LEN)
        got = ver(buf, VERSION_LEN)
        if got != AUTH_INTERFACE:
            raise AuthError(f"{path} has interface {got}, this feeder needs {AUTH_INTERFACE}")
        try:
            version = buf.value.decode("ascii")
        except UnicodeDecodeError:
            version = ""
        if not version or not version.isprintable():
            raise AuthError(f"{path} reports no usable version string")
        self.version = version
        self._short, self._spot = short, spot

    def _hash(self, fn: Any, text: str) -> str | None:
        out = ctypes.create_string_buffer(HASH_LEN)
        if fn(text.encode("utf-8"), out, HASH_LEN) != 0:
            return None
        h = out.value.decode("ascii", errors="replace")
        if len(h) != HASH_CHARS or not all(c in string.hexdigits for c in h):
            log.error("auth library returned a malformed hash: treated as a refusal")
            return None
        return h

    def short_hash(self, sign_in: str) -> str | None:
        return self._hash(self._short, sign_in)

    def spot_hash(self, call: str) -> str | None:
        return self._hash(self._spot, call)


def sparkgap_version() -> str | None:
    """SparkGap/<short commit> of the checkout this file runs from; None outside git."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    commit = out.stdout.strip()
    return f"SparkGap/{commit}" if out.returncode == 0 and commit else None


def ntp_synced() -> bool:
    """The system clock is NTP-synchronised (timedatectl); False when that can't be read."""
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.stdout.strip() == "yes"


def not_ready(auth: Auth | None, version: str | None, dry_run: bool) -> str | None:
    """Why the feeder must not post, or None. A dry run posts nothing, so it always may run."""
    if dry_run:
        return None
    if auth is None:
        return "no auth library (--auth-lib): RBN would drop these spots"
    if version is None:
        return "no SparkGap version (not a git checkout): the sign-in must name the commit"
    return None


class Session:
    """id.php and s.php for one node."""

    def __init__(
        self,
        *,
        url: str,
        call: str,
        name: str,
        grid: str,
        qth: str,
        validation: str,
        port: int,
        bands: list[tuple[float, float]],
        auth: Auth | None,
        version: str,
        fingerprint: str,
        dry_run: bool,
    ) -> None:
        if auth is None and not dry_run:
            raise AuthError("no auth library: a session without one may only dry-run")
        self.url = url.rstrip("/")
        self.call, self.name, self.grid, self.qth = call, name, grid, qth
        self.validation, self.port, self.bands = validation, port, bands
        self.auth, self.version, self.fingerprint, self.dry_run = (
            auth,
            version,
            fingerprint,
            dry_run,
        )
        self.agg = auth.version if auth else "rbn2auth/none"
        self.registered = threading.Event()  # set while the last id.php succeeded
        self.ntp = False  # the clock state the last id.php reported

    def sign_in(self) -> str:
        return f"{self.version} is operated by {self.name}, {self.call}"

    def id_body(self) -> dict[str, Any] | None:
        """The id.php body, or None when the sign-in could not be hashed."""
        bands = ",".join(f"{lo:.1f}-{hi:.1f}" for lo, hi in self.bands)
        sign_in = self.sign_in()
        short = self.auth.short_hash(sign_in) if self.auth else ""
        if short is None:
            log.error("auth library refused the sign-in %r: id.php not sent", sign_in)
            return None
        self.ntp = ntp_synced()
        return {
            "ClockNtp": self.ntp,
            "ClockNtpDiff": 0,
            "SkimPort": str(self.port),
            "aggVersion": self.agg,
            "bandLimits": bands,
            "cWBandLimits": "",
            "fTBandLimits": "",
            "fingerPrint": self.fingerprint,
            "justCQ": "1",
            "mIXEDBandLimits": bands,
            "masterSCPFilter": "0",
            "rTTYBandLimits": "",
            "showIP": "1",
            "skimCall": self.call,
            "skimGrid": self.grid,
            "skimName": self.name,
            "skimQth": self.qth,
            "skimSignIn": sign_in,
            "skimValLevel": self.validation,
            "skimVersion": self.version,
            "shortHash": short,
            "t": "id",
        }

    def spot_body(self, spot: dict[str, Any], now: float) -> dict[str, Any] | None:
        """The s.php body for one spot, or None when its call could not be hashed (dropped)."""
        h = self.auth.spot_hash(spot["call"]) if self.auth else ""
        if h is None:
            log.error("auth library refused call %r: spot dropped", spot["call"])
            return None
        row = [
            f"{spot['freq']:.2f}",
            spot["call"],
            CQ_CODE.get(spot["cq_flag"], "1"),
            str(spot["snr"]),
            str(spot["wpm"]),
            "dB",
            MODE_CODE.get(spot["mode"], "1"),
            spot["cq_flag"],
            "D:",
        ]
        return {
            "agg": self.agg,
            "e": self.call,
            "fp": self.fingerprint,
            "h": h,
            "nTP": self.ntp,
            "tm": now,
            "s": [row],
            "t": "s",
        }

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        data = json.dumps(body, separators=(",", ":"))
        if self.dry_run:
            log.info("[DRY] POST %s %s", path, data[:300])
            return None
        req = urlreq.Request(
            f"{self.url}/{path}",
            data=data.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Connection": "Keep-Alive",
                "User-Agent": f"rbn2_feeder/{self.version}",
            },
        )
        try:
            with urlreq.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                got = json.loads(resp.read() or b"{}")
        except (urlerror.URLError, OSError) as e:
            log.warning("%s failed: %s", path, e)
            return None
        except ValueError:  # a reply that is not JSON text: the post itself went through
            return {}
        return got if isinstance(got, dict) else {}

    def post_id(self) -> bool:
        """Sign in (or heartbeat); spots go out only while the last one succeeded."""
        body = self.id_body()
        resp = self.post("id.php", body) if body is not None else None
        ok = body is not None and (resp is not None or self.dry_run)
        if ok:
            self.registered.set()
        else:
            self.registered.clear()
        if resp:
            log.info("id.php reply: %s", resp)
        return ok


def upload_loop(
    sess: Session,
    spots: "queue.Queue[dict[str, Any]]",
    blacklist: set[str],
    shutdown: threading.Event,
    clock: Callable[[], float],
) -> None:
    """Post each spot as it arrives, one per post."""
    counts = {"sent": 0, "blacklisted": 0, "dropped": 0, "not signed in": 0}
    while not shutdown.is_set():
        try:
            spot = spots.get(timeout=QUEUE_POLL_S)
        except queue.Empty:
            continue
        if spot["call"] in blacklist:
            counts["blacklisted"] += 1
            continue
        if not sess.registered.is_set():  # dropped, not held: no backlog of old spots later
            counts["not signed in"] += 1
            continue
        body = sess.spot_body(spot, clock())
        if body is None or (sess.post("s.php", body) is None and not sess.dry_run):
            counts["dropped"] += 1
        else:
            counts["sent"] += 1
        log.info("%s %.2f: %s", spot["call"], spot["freq"], counts)


def heartbeat_loop(sess: Session, shutdown: threading.Event) -> None:
    while not shutdown.wait(HEARTBEAT_S if sess.post_id() else ID_RETRY_S):
        pass


def main() -> None:
    p = argparse.ArgumentParser(description="SparkGap's RBN feeder (librbn2_auth)")
    p.add_argument("--call", required=True, help="the node's spotter ID, e.g. N0CALL-1")
    p.add_argument("--name", default="SparkGap", help="operator name for the sign-in")
    p.add_argument("--grid", required=True, help="the node's locator, e.g. FN31pr")
    p.add_argument("--qth", default="")
    p.add_argument(
        "--validation",
        default="Normal",
        choices=["Permissive", "Normal", "Aggressive"],
        help="the spot_validation level SparkGap runs with",
    )
    p.add_argument("--auth-lib", help="path to librbn2_auth.so, kept outside every SparkGap clone")
    p.add_argument("--local-host", default="127.0.0.1")
    p.add_argument("--local-port", type=int, default=7300)
    p.add_argument("--rbn-url", default=base.DEFAULT_RBN_URL)
    p.add_argument("--blacklist", default="blacklist.txt")
    p.add_argument("--dry-run", action="store_true", help="log what would be posted; post nothing")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    a = p.parse_args()
    logging.basicConfig(
        level=getattr(logging, a.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    auth = None
    if a.auth_lib:
        try:
            auth = Auth(a.auth_lib)
        except AuthError as e:
            sys.exit(f"rbn2_feeder: {e}")
        log.info("auth library %s", auth.version)
    version = sparkgap_version()
    why = not_ready(auth, version, a.dry_run)
    if why:
        sys.exit(f"rbn2_feeder: not posting: {why}")

    bands = base.fetch_bands_from_skimmer(a.local_host, a.local_port, a.call)
    if not bands:
        log.warning("no band info from the skimmer's SETT: empty bandLimits")
    fingerprint = hashlib.md5(f"{a.call}@{socket.gethostname()}".encode()).hexdigest()
    sess = Session(
        url=a.rbn_url,
        call=a.call,
        name=a.name,
        grid=a.grid,
        qth=a.qth,
        validation=a.validation,
        port=a.local_port,
        bands=bands,
        auth=auth,
        version=version or "SparkGap/unknown",
        fingerprint=fingerprint,
        dry_run=a.dry_run,
    )
    log.info("signing in as %r", sess.sign_in())

    spots: queue.Queue[dict[str, Any]] = queue.Queue()
    shutdown = threading.Event()
    threads = [
        threading.Thread(
            target=base.read_local,
            args=(a.local_host, a.local_port, a.call, spots, shutdown),
            name="read_local",
            daemon=True,
        ),
        threading.Thread(
            target=upload_loop,
            args=(sess, spots, base.load_blacklist(a.blacklist), shutdown, time.time),
            name="upload",
            daemon=True,
        ),
        threading.Thread(
            target=heartbeat_loop, args=(sess, shutdown), name="heartbeat", daemon=True
        ),
    ]
    for t in threads:
        t.start()
    try:
        while not shutdown.wait(1.0):
            pass
    except KeyboardInterrupt:
        shutdown.set()


if __name__ == "__main__":
    main()
