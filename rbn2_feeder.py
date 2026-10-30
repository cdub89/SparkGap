#!/usr/bin/env python3
"""rbn2_feeder.py: SparkGap's RBN feeder, authenticated by the private librbn2_auth library.

Reads SparkGap's local cluster telnet like rbn_feeder.py (whose spot parsing and band probe it
reuses) and posts to RBN's id.php and s.php. The two hash fields come from the
library librbn2_auth.so in the run root (the folder above this file's), handed out separately and
never kept in a git checkout.
Without a library of the expected version the feeder does not post (--dry-run aside).

Differences from rbn_feeder.py: heartbeat every 300 s;
SparkGap signs in as SKIM_VERSION; RBN lists it as skimmer SKIM_VERSION, Aggregator AGG_VERSION.

Identity and settings come from the SparkGap config; its rbn_upload key ("dry" or "live", off
when absent) decides whether it posts.

usage: rbn2_feeder.py --config config/<seat>.local.json [--dry-run]
"""

import argparse
import ctypes
import hashlib
import http.client
import json
import logging
import queue
import signal
import socket
import string
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlreq

import rbn_feeder as base

log = logging.getLogger("rbn2_feeder")

AUTH_VERSION = "rbn2auth/1.2"  # RBN2_AUTH_VERSION, compared as text
AUTH_LIB = "librbn2_auth.so"
SKIM_VERSION = "SparkGap 1.2"  # skimVersion and sign-in: names SparkGap in RBN's node list
AGG_VERSION = "6.7"  # id.php aggVersion and s.php agg: RBN's interim value for direct feeders
UPLOAD_MODES = ("dry", "live")  # rbn_upload; absent or anything else is off
VALIDATION = {"permissive": "Permissive", "normal": "Normal", "aggressive": "Aggressive"}
VALIDATION_OFF = (None, False, "off", "")  # sparkgap.py's off values
UPLOAD_S = 10.0  # Aggregator's tick when id.php names no serverUploadInterval
VERSION_LEN = 64
HASH_LEN = 16
HASH_CHARS = 8
HEARTBEAT_S = 300.0
ID_RETRY_S = 60.0  # after a failed id.php; spots wait until it succeeds
HTTP_TIMEOUT_S = 15
READ_TIMEOUT_S = 60.0
RECONNECT_S = 5.0
MODE_CODE = {"CW": "1", "RTTY": "11"}  # Aggregator 6.7's mode table
CQ_CODE = {"CQ": "1", "DX": "2", "BCN": "3", "BEACON": "3"}
HIDDEN = ("fingerPrint", "fp", "h", "shortHash", "skimSignIn")  # never in a log


def masked(body: dict[str, Any]) -> dict[str, Any]:
    """body with the HIDDEN fields replaced, for logging."""
    return {k: "<hidden>" if k in HIDDEN else v for k, v in body.items()}


class AuthError(RuntimeError):
    """The auth library is missing or not the version this feeder expects."""


class Auth:
    """librbn2_auth through ctypes: its version string and the hashes."""

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
        short.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        spot.argtypes = [
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        short.restype = spot.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(VERSION_LEN)
        ret = ver(buf, VERSION_LEN)
        if ret != 0 or buf.value != AUTH_VERSION.encode("ascii"):
            got = buf.value.decode("ascii", errors="replace")
            raise AuthError(
                f"{path} is version {got!r} (returned {ret}), this feeder takes {AUTH_VERSION}"
            )
        self.version = AUTH_VERSION
        self._short, self._spot = short, spot

    def _hash(self, fn: Any, *args: Any) -> str | None:
        out = ctypes.create_string_buffer(HASH_LEN)
        if fn(*args, out, HASH_LEN) != 0:
            return None
        h = out.value.decode("ascii", errors="replace")
        if len(h) != HASH_CHARS or not all(c in string.hexdigits for c in h):
            log.error("auth library returned a malformed hash: treated as a refusal")
            return None
        return h

    def short_hash(self, sign_in: str) -> str | None:
        return self._hash(self._short, sign_in.encode("utf-8"))

    def spot_hash(self, calls: list[str]) -> str | None:
        """h for a post of these calls, in row order; None when empty or refused."""
        if not calls:
            return None
        arr = (ctypes.c_char_p * len(calls))(*(c.encode("utf-8") for c in calls))
        return self._hash(self._spot, arr, len(calls))


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


@dataclass
class Seat:
    """What the feeder takes from a seat config."""

    call: str
    grid: str
    name: str
    qth: str
    validation: str
    host: str
    port: int
    mode: str  # "dry" or "live"


def seat_settings(cfg: dict[str, Any]) -> Seat:
    """The feeder's settings from a seat config; ValueError says why it can't feed."""
    mode = cfg.get("rbn_upload")
    if mode not in UPLOAD_MODES:
        raise ValueError(f"rbn_upload is {mode!r}: off")
    call, grid = cfg.get("callsign"), cfg.get("grid")
    if not call or not grid:
        raise ValueError("callsign and grid must both be set")
    level = cfg.get("spot_validation", "normal")  # sparkgap.py's default
    if level not in VALIDATION and level not in VALIDATION_OFF:
        raise ValueError(f"spot_validation is {level!r}: not a level sparkgap.py knows")
    return Seat(
        call=call,
        grid=grid,
        name=cfg.get("op_name") or "",
        qth=cfg.get("qth") or "",
        validation="Off" if level in VALIDATION_OFF else VALIDATION[level],
        host=cfg.get("telnet_host") or "127.0.0.1",
        port=int(cfg.get("telnet_port", 7300)),
        mode=mode,
    )


def auth_lib_path() -> Path:
    """librbn2_auth.so in the folder above this checkout, never inside it."""
    return Path(__file__).resolve().parent.parent / AUTH_LIB


def not_ready(auth: Auth | None, dry_run: bool) -> str | None:
    """Why the feeder must not post, or None. A dry run posts nothing, so it always may run."""
    if dry_run:
        return None
    if auth is None:
        return f"no {auth_lib_path()}: RBN would drop these spots"
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
        self.agg = AGG_VERSION
        self.upload_s = UPLOAD_S  # the server's serverUploadInterval once id.php names one
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
            log.error("auth library refused the sign-in: id.php not sent")
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
            "skimVersion": SKIM_VERSION,
            "shortHash": short,
            "t": "id",
        }

    def spots_body(self, spots: list[dict[str, Any]], now: float) -> dict[str, Any] | None:
        """The s.php body for these spots, or None when their calls can't be hashed (dropped)."""
        calls = [spot["call"] for spot in spots]
        h = self.auth.spot_hash(calls) if self.auth else ""
        if h is None:
            log.error("auth library refused calls %s: spots dropped", calls)
            return None
        rows = [
            [
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
            for spot in spots
        ]
        return {
            "agg": self.agg,
            "e": self.call,
            "fp": self.fingerprint,
            "h": h,
            "nTP": self.ntp,
            "tm": now,
            "s": rows,
            "t": "s",
        }

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        data = json.dumps(body, separators=(",", ":"))
        if self.dry_run:
            log.info("[DRY] POST %s %s", path, json.dumps(masked(body), separators=(",", ":")))
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
        except (urlerror.URLError, OSError, http.client.HTTPException) as e:
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
            log.info("id.php reply: %s", masked(resp))
            interval = resp.get("serverUploadInterval")
            try:
                if interval is not None and float(interval) > 0:
                    self.upload_s = float(interval)
            except (TypeError, ValueError):
                log.warning(
                    "serverUploadInterval %r unusable: tick stays %.0f s", interval, self.upload_s
                )
        return ok


def upload_loop(
    sess: Session,
    spots: "queue.Queue[dict[str, Any]]",
    blacklist: set[str],
    shutdown: threading.Event,
    clock: Callable[[], float],
) -> None:
    """Each upload tick, one post of every spot queued since the last (rbn_feeder.upload_loop)."""
    counts = {"sent": 0, "blacklisted": 0, "dropped": 0, "not signed in": 0}
    while not shutdown.wait(sess.upload_s):
        batch: list[dict[str, Any]] = []
        while True:
            try:
                spot = spots.get_nowait()
            except queue.Empty:
                break
            if spot["call"] in blacklist:
                counts["blacklisted"] += 1
            else:
                batch.append(spot)
        if not batch:
            continue
        if not sess.registered.is_set():  # dropped, not held: no backlog of old spots later
            counts["not signed in"] += len(batch)
            continue
        body = sess.spots_body(batch, clock())
        if body is None or (sess.post("s.php", body) is None and not sess.dry_run):
            counts["dropped"] += len(batch)
        else:
            counts["sent"] += len(batch)
        log.info("%d spots: %s: %s", len(batch), " ".join(s["call"] for s in batch), counts)


def read_local(
    host: str,
    port: int,
    call: str,
    spots: queue.Queue[dict[str, Any]],
    shutdown: threading.Event,
    connect: Callable[..., socket.socket] = socket.create_connection,
) -> None:
    """SparkGap's telnet into spots. A quiet minute is not a lost connection: only EOF or a
    socket error reconnects, since a spot sent while disconnected never arrives."""
    while not shutdown.is_set():
        try:
            with connect((host, port), timeout=READ_TIMEOUT_S) as s:
                log.info("connected to local skimmer %s:%d", host, port)
                s.sendall(f"{call}\n".encode("ascii"))
                buf = b""
                while not shutdown.is_set():
                    try:
                        chunk = s.recv(8192)
                    except TimeoutError:
                        continue
                    if not chunk:
                        log.warning("local skimmer disconnected (EOF)")
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        text = line.decode("ascii", errors="replace").rstrip("\r")
                        if "DX de" in text:
                            spot = base.parse_spot(text)
                            if spot is not None and base.uploadable(spot):
                                spots.put(spot)
        except OSError as e:
            log.warning("local read error: %s: reconnecting in %.0f s", e, RECONNECT_S)
        if not shutdown.is_set():
            shutdown.wait(RECONNECT_S)


def worker(target: Callable[..., None], shutdown: threading.Event, failed: threading.Event):
    """target in a thread that stops the feeder if it dies, so it never runs on without it."""

    def run(*args: Any) -> None:
        name = threading.current_thread().name
        try:
            target(*args)
        except Exception:
            log.exception("%s thread failed: the feeder stops", name)
        else:
            if shutdown.is_set():
                return
            log.error("%s thread ended unasked: the feeder stops", name)
        failed.set()
        shutdown.set()

    return run


def heartbeat_loop(sess: Session, shutdown: threading.Event) -> None:
    while not shutdown.wait(HEARTBEAT_S if sess.post_id() else ID_RETRY_S):
        pass


def wait_for_stop(shutdown: threading.Event, failed: threading.Event) -> bool:
    """Runs until Ctrl-C, SIGTERM or a failed thread and logs how it stopped; True when clean."""
    by: list[str] = []

    def on_sigterm(_signum: int, _frame: Any) -> None:
        by.append("SIGTERM")
        shutdown.set()

    old = signal.signal(signal.SIGTERM, on_sigterm)
    try:
        while not shutdown.wait(1.0):
            pass
    except KeyboardInterrupt:
        by.append("Ctrl-C")
        shutdown.set()
    finally:
        signal.signal(signal.SIGTERM, old)
    if failed.is_set():
        log.error("stopped: a thread failed, see above")
        return False
    log.info("stopped (%s)", by[0] if by else "shutdown")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="SparkGap's RBN feeder (librbn2_auth)")
    p.add_argument("--config", required=True, help="the seat config, config/<seat>.local.json")
    p.add_argument("--rbn-url", default=base.DEFAULT_RBN_URL)
    p.add_argument("--blacklist", default="blacklist.txt")
    p.add_argument("--dry-run", action="store_true", help="post nothing, whatever rbn_upload says")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    a = p.parse_args()
    logging.basicConfig(
        level=getattr(logging, a.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        cfg = json.loads(Path(a.config).read_text())
        if a.dry_run:
            cfg["rbn_upload"] = "dry"
        seat = seat_settings(cfg)
    except (OSError, ValueError) as e:
        sys.exit(f"rbn2_feeder: {a.config}: {e}")
    dry_run = seat.mode == "dry"

    auth = None
    lib = auth_lib_path()
    if lib.exists():
        try:
            auth = Auth(str(lib))
        except AuthError as e:
            sys.exit(f"rbn2_feeder: {e}")
        log.info("auth library %s from %s", auth.version, lib)
    why = not_ready(auth, dry_run)
    if why:
        sys.exit(f"rbn2_feeder: not posting: {why}")

    bands = base.fetch_bands_from_skimmer(seat.host, seat.port, seat.call)
    if not bands:
        log.warning("no band info from the skimmer's SETT: empty bandLimits")
    fingerprint = hashlib.md5(f"{seat.call}@{socket.gethostname()}".encode()).hexdigest()
    sess = Session(
        url=a.rbn_url,
        call=seat.call,
        name=seat.name,
        grid=seat.grid,
        qth=seat.qth,
        validation=seat.validation,
        port=seat.port,
        bands=bands,
        auth=auth,
        version=SKIM_VERSION,
        fingerprint=fingerprint,
        dry_run=dry_run,
    )
    mode = "dry run" if dry_run else "LIVE"
    log.info("SparkGap version %s, spotter %s, %s", sess.version, sess.call, mode)

    spots: queue.Queue[dict[str, Any]] = queue.Queue()
    shutdown, failed = threading.Event(), threading.Event()
    threads = [
        threading.Thread(
            target=worker(read_local, shutdown, failed),
            args=(seat.host, seat.port, seat.call, spots, shutdown),
            name="read_local",
            daemon=True,
        ),
        threading.Thread(
            target=worker(upload_loop, shutdown, failed),
            args=(sess, spots, base.load_blacklist(a.blacklist), shutdown, time.time),
            name="upload",
            daemon=True,
        ),
        threading.Thread(
            target=worker(heartbeat_loop, shutdown, failed),
            args=(sess, shutdown),
            name="heartbeat",
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()
    if not wait_for_stop(shutdown, failed):
        sys.exit("rbn2_feeder: a thread failed, see the log")


if __name__ == "__main__":
    main()
