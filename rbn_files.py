"""RBN's reference files, downloaded into rbn_known_dir as the Aggregator downloads them.

RBN's sign-in reply names them; both are public and fetched at most once a day, and a failed or
empty fetch keeps the previous copy:

- patt3ch.lst (PATT3CH_URL): CW Skimmer's call patterns, maintained for RBN by the Aggregator's
  author. The tracker reads it (2, 3 or 4 reads by pattern); without a download it reads the copy
  shipped in the checkout.
- bcn.csv (BCN_URL): every beacon RBN lists, `frequency,call` per line, fetched with the Gated
  Rule. No gate reads it yet: cdub89/SparkGap#56 decides how gate 12 uses it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

import downloads

PATT3CH_URL = "https://data.reversebeacon.net/downloads/patt3ch/patt3ch.lst"
BCN_URL = "https://data.reversebeacon.net/downloads/beacon/bcn.csv"
PATT3CH_FILE = "patt3ch.lst"
BCN_FILE = "bcn.csv"
MAX_AGE_S = 24 * 3600.0
MIN_PATTERNS = 10_000  # a download with fewer is not RBN's patt3ch.lst
MIN_BEACONS = 1_000    # a download with fewer rows is not RBN's beacon list
FETCH_TIMEOUT_S = 30.0
USER_AGENT = "SparkGap rbn_files"
PATTERN = re.compile(r"[+ ] +[0-9A-Z#@]+", re.IGNORECASE)  # "+ K1@@": + marks a common one
BEACON = re.compile(r"\d+,[A-Z0-9/-]+", re.IGNORECASE)


def _rows(path: Path, row: re.Pattern[str], least: int, what: str) -> int:
    """The file's row count; any row not of its form, or too few rows, is not the file."""
    rows = [r.rstrip() for r in path.read_text(errors="replace").splitlines() if r.strip()]
    bad = next((r for r in rows if not row.fullmatch(r)), None)
    if bad is not None:
        raise ValueError(f"not a {what} row: {bad[:40]!r}")
    if len(rows) < least:
        raise ValueError(f"{len(rows)} {what}s")
    return len(rows)


def _check_patt3ch(path: Path) -> int:
    return _rows(path, PATTERN, MIN_PATTERNS, "pattern")


def _check_beacons(path: Path) -> int:
    return _rows(path, BEACON, MIN_BEACONS, "beacon")


def refresh_patt3ch(cache_dir: Path, fetch: Callable[[], bytes] | None = None,
                    now: Callable[[], float] = time.time) -> Path | None:
    """patt3ch.lst fetched at most once a day; its path, or None when no copy passes its check."""
    path = cache_dir / PATT3CH_FILE
    downloads.refresh(
        path, PATT3CH_URL, _check_patt3ch, "patterns", USER_AGENT, max_age_s=MAX_AGE_S,
        get=fetch or (lambda: downloads.fetch(PATT3CH_URL, USER_AGENT, FETCH_TIMEOUT_S)), now=now)
    try:
        _check_patt3ch(path)
    except downloads.FAILED:
        return None
    return path


def refresh_beacons(cache_dir: Path, fetch: Callable[[], bytes] | None = None,
                    now: Callable[[], float] = time.time) -> bool:
    """bcn.csv fetched at most once a day; whether a copy is there."""
    return downloads.refresh(
        cache_dir / BCN_FILE, BCN_URL, _check_beacons, "beacons", USER_AGENT, max_age_s=MAX_AGE_S,
        get=fetch or (lambda: downloads.fetch(BCN_URL, USER_AGENT, FETCH_TIMEOUT_S)), now=now)
