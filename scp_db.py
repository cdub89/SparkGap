"""Super Check Partial calls (SCP.DB), for spot_rule_scp.

SCP.DB (SCP_URL) is the SQLite form of Super Check Partial: every call seen in the contest logs it
is built from, about 69,000, where MASTER.SCP keeps the 50,000 most active. It is fetched when
SparkGap starts, at most once a day, into the cache directory; offline, the cached copy is used.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import downloads

SCP_URL = "https://www.supercheckpartial.com/SCP.DB"
FILE_NAME = "SCP.DB"
MAX_AGE_S = 24 * 3600.0
MIN_CALLS = 20_000              # a download with fewer is not SCP.DB, and never replaces the cache
FETCH_TIMEOUT_S = 60.0
USER_AGENT = "SparkGap scp_db"

log = logging.getLogger("sparkgap")


def calls(path: Path) -> frozenset[str]:
    """Every call in an SCP.DB file (its callsigns table), read-only."""
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"          # escapes a path with spaces, # or ?
    con = sqlite3.connect(uri, uri=True)
    try:
        return frozenset(c.upper() for (c,) in con.execute("SELECT callsign FROM callsigns"))
    finally:
        con.close()


def _fetch() -> bytes:
    return downloads.fetch(SCP_URL, USER_AGENT, FETCH_TIMEOUT_S)


def _check(path: Path) -> int:
    """SCP.DB's call count; fewer than MIN_CALLS is not SCP.DB."""
    n = len(calls(path))
    if n < MIN_CALLS:
        raise ValueError(f"{n} calls")
    return n


def refresh(cache_dir: Path, fetch: Callable[[], bytes] | None = None,
            now: Callable[[], float] = time.time) -> frozenset[str]:
    """SCP calls, from SCP.DB fetched at most once a day; the cache when offline; else empty."""
    path = cache_dir / FILE_NAME
    downloads.refresh(path, SCP_URL, _check, "calls", USER_AGENT, max_age_s=MAX_AGE_S,
                      get=fetch or _fetch, now=now)
    try:
        found = calls(path) if path.exists() else frozenset()
    except sqlite3.Error as e:
        log.warning("scp_db: %s unreadable (%r)", path, e)
        found = frozenset()
    log.info("scp_db: %d SCP calls", len(found))
    return found
