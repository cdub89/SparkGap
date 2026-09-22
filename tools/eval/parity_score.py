#!/usr/bin/env python3
"""Decode-level parity scorer for SparkGap against CW Skimmer.

Compares what SparkGap decoded and spotted against what CW Skimmer spotted
from the same radio at the same time. Three input shapes: a CW Skimmer
telnet tee (DX de spot lines), our raw ITILA decode lines, and our own
spot lines (replay SPOT: lines, or a live telnet tee, itself DX de lines).
A CW Skimmer spot counts as decoded if a raw bin within the frequency
tolerance contains its base call as a whole token, and counts as spotted
if one of our spots matches the same call within tolerance. Our spots
count as precise if a CW Skimmer spot confirms them the same way.

Capture procedure:
1. Run CW Skimmer live on one DAX IQ channel, tee its telnet output to a
   file with tools/capture_cluster.py.
2. Run SparkGap live on another DAX IQ channel of the same radio, with
   record_wav set in the config, its log redirected to a file.
3. Run both at the same wall clock time.
4. Replay the recording with sparkgap.py --file <wav> --center-khz <c>
   under any change and score the replay log against the same CW
   Skimmer tee.
"""
import argparse
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

PASSBAND_FRACTION = 0.25
DEFAULT_TOL_KHZ = 0.5
DEFAULT_SHOW = 20
MISS_TEXT_TRUNCATE = 60
DEDUPE_ROUND_NDIGITS = 1

# CW Skimmer's own telnet port omits the mode column; tees relayed through a
# cluster or our own server carry one, so the mode is optional.
DX_DE_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})\s+DX de \S+:\s+(?P<freq>[\d.]+)\s+"
    r"(?P<call>\S+)\s+(?:(?P<mode>[A-Z]+)\s+)?(?P<rest>\d+ dB.*)$"
)
RAW_DECODE_RE = re.compile(r"ITILA raw\s+(?P<freq>[\d.]+)\s+kHz.*'(?P<text>.*)'$")
SPOT_LINE_RE = re.compile(r"SPOT:\s+(?P<freq>[\d.]+)\s+(?P<call>\S+)\s+(?P<snr>\d+) dB")
CQ_TOKEN_RE = re.compile(r"(?:^|\s)CQ(?:\s|$)")


def has_cq(rest: str) -> bool:
    return CQ_TOKEN_RE.search(rest) is not None


@dataclass(frozen=True)
class Spot:
    freq_khz: float
    call: str
    cq: bool


@dataclass(frozen=True)
class RawDecode:
    freq_khz: float
    text: str


@dataclass
class OurLog:
    raw: list[RawDecode]
    spots: list[Spot]


@dataclass(frozen=True)
class Miss:
    freq_khz: float
    call: str
    cq: bool
    nearest_freq_khz: float | None
    nearest_text: str
    within_tol: bool


@dataclass
class Score:
    decode_hit: int
    decode_total: int
    spot_hit: int
    spot_total: int
    precision_hit: int
    precision_total: int
    decode_cq_hit: int
    decode_cq_total: int
    spot_cq_hit: int
    spot_cq_total: int
    misses: list[Miss]
    unconfirmed: list[Spot]
    passband: tuple[float, float]
    tol_khz: float


def base_call(raw: str) -> str:
    return raw.split("/")[0].upper()


def in_passband(freq_khz: float, lo: float, hi: float) -> bool:
    return lo <= freq_khz <= hi


def token_match(call: str, text: str) -> bool:
    pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(call)}(?![A-Z0-9])")
    return pattern.search(text.upper()) is not None


def dedupe_spots(spots: list[Spot]) -> list[Spot]:
    merged: dict[tuple[str, float], Spot] = {}
    for spot in spots:
        key = (spot.call, round(spot.freq_khz, DEDUPE_ROUND_NDIGITS))
        existing = merged.get(key)
        if existing is None:
            merged[key] = spot
        elif spot.cq and not existing.cq:
            merged[key] = replace(existing, cq=True)
    return list(merged.values())


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        raise


def parse_cws_tee(path: Path) -> list[Spot]:
    spots = []
    for line in read_lines(path):
        m = DX_DE_RE.match(line)
        if not m or m.group("mode") not in (None, "CW"):
            continue
        freq, call, rest = m.group("freq"), m.group("call"), m.group("rest")
        spots.append(Spot(float(freq), base_call(call), has_cq(rest)))
    return spots


def parse_ours_log(path: Path) -> OurLog:
    raw: list[RawDecode] = []
    spots: list[Spot] = []
    for line in read_lines(path):
        m_raw = RAW_DECODE_RE.search(line)
        if m_raw:
            raw.append(RawDecode(float(m_raw.group("freq")), m_raw.group("text")))
            continue
        m_spot = SPOT_LINE_RE.search(line)
        if m_spot:
            spots.append(Spot(float(m_spot.group("freq")), base_call(m_spot.group("call")), False))
            continue
        m_dx = DX_DE_RE.match(line)
        if m_dx and m_dx.group("mode") in (None, "CW"):
            freq, call, rest = m_dx.group("freq"), m_dx.group("call"), m_dx.group("rest")
            spots.append(Spot(float(freq), base_call(call), has_cq(rest)))
    return OurLog(raw=raw, spots=dedupe_spots(spots))


def compute_passband(
    center_khz: float, rate: int, override: tuple[float, float] | None
) -> tuple[float, float]:
    if override is not None:
        return override
    half_width = rate * PASSBAND_FRACTION / 1000.0
    return center_khz - half_width, center_khz + half_width


def _decode_confirmed(spot: Spot, raw: list[RawDecode], tol_khz: float) -> bool:
    return any(
        abs(r.freq_khz - spot.freq_khz) <= tol_khz and token_match(spot.call, r.text) for r in raw
    )


def _spot_confirmed(spot: Spot, candidates: list[Spot], tol_khz: float) -> bool:
    return any(
        c.call == spot.call and abs(c.freq_khz - spot.freq_khz) <= tol_khz for c in candidates
    )


def decode_recall(cws_spots: list[Spot], raw: list[RawDecode], tol_khz: float) -> tuple[int, int]:
    hit = sum(1 for s in cws_spots if _decode_confirmed(s, raw, tol_khz))
    return hit, len(cws_spots)


def spot_recall(cws_spots: list[Spot], our_spots: list[Spot], tol_khz: float) -> tuple[int, int]:
    hit = sum(1 for s in cws_spots if _spot_confirmed(s, our_spots, tol_khz))
    return hit, len(cws_spots)


def spot_precision(our_spots: list[Spot], cws_spots: list[Spot], tol_khz: float) -> tuple[int, int]:
    hit = sum(1 for s in our_spots if _spot_confirmed(s, cws_spots, tol_khz))
    return hit, len(our_spots)


def find_misses(cws_spots: list[Spot], raw: list[RawDecode], tol_khz: float) -> list[Miss]:
    misses = []
    for spot in cws_spots:
        if _decode_confirmed(spot, raw, tol_khz):
            continue
        if not raw:
            misses.append(Miss(spot.freq_khz, spot.call, spot.cq, None, "", False))
            continue
        nearest = min(raw, key=lambda r: abs(r.freq_khz - spot.freq_khz))
        offset = abs(nearest.freq_khz - spot.freq_khz)
        text = nearest.text[:MISS_TEXT_TRUNCATE]
        within = offset <= tol_khz
        misses.append(Miss(spot.freq_khz, spot.call, spot.cq, nearest.freq_khz, text, within))
    misses.sort(key=lambda m: m.freq_khz)
    return misses


def find_unconfirmed(our_spots: list[Spot], cws_spots: list[Spot], tol_khz: float) -> list[Spot]:
    result = [s for s in our_spots if not _spot_confirmed(s, cws_spots, tol_khz)]
    result.sort(key=lambda s: s.freq_khz)
    return result


def compute_score(
    ours: OurLog, cws_spots_all: list[Spot], lo: float, hi: float, tol_khz: float
) -> Score:
    raw = [r for r in ours.raw if in_passband(r.freq_khz, lo, hi)]
    our_spots = [s for s in ours.spots if in_passband(s.freq_khz, lo, hi)]
    cws_spots = dedupe_spots([s for s in cws_spots_all if in_passband(s.freq_khz, lo, hi)])
    cws_cq = [s for s in cws_spots if s.cq]

    decode_hit, decode_total = decode_recall(cws_spots, raw, tol_khz)
    spot_hit, spot_total = spot_recall(cws_spots, our_spots, tol_khz)
    precision_hit, precision_total = spot_precision(our_spots, cws_spots, tol_khz)
    decode_cq_hit, decode_cq_total = decode_recall(cws_cq, raw, tol_khz)
    spot_cq_hit, spot_cq_total = spot_recall(cws_cq, our_spots, tol_khz)

    return Score(
        decode_hit, decode_total, spot_hit, spot_total, precision_hit, precision_total,
        decode_cq_hit, decode_cq_total, spot_cq_hit, spot_cq_total,
        find_misses(cws_spots, raw, tol_khz), find_unconfirmed(our_spots, cws_spots, tol_khz),
        (lo, hi), tol_khz,
    )


def pct_str(hit: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{100.0 * hit / total:.1f}%"


def format_miss(miss: Miss) -> str:
    cq_marker = " CQ" if miss.cq else ""
    if miss.nearest_freq_khz is None:
        nearest = "nearest none"
    elif miss.within_tol:
        nearest = f"nearest {miss.nearest_freq_khz:.1f}"
    else:
        offset = abs(miss.nearest_freq_khz - miss.freq_khz)
        nearest = f"nearest {miss.nearest_freq_khz:.1f} (off by {offset:.2f} kHz)"
    return f"  {miss.freq_khz:.1f} {miss.call}{cq_marker}   {nearest}: '{miss.nearest_text}'"


def summary_line(name: str, score: Score) -> str:
    lo, hi = score.passband
    return (
        f"{name}: decode recall {score.decode_hit}/{score.decode_total} "
        f"({pct_str(score.decode_hit, score.decode_total)}) | "
        f"spot recall {score.spot_hit}/{score.spot_total} | "
        f"spot precision {score.precision_hit}/{score.precision_total} | "
        f"CQ: decode {score.decode_cq_hit}/{score.decode_cq_total}, "
        f"spot {score.spot_cq_hit}/{score.spot_cq_total} | "
        f"passband {lo:.2f}-{hi:.2f} tol {score.tol_khz:g}"
    )


def format_score_block(name: str, score: Score, show: int) -> list[str]:
    lines = [summary_line(name, score)]
    lines.append(f"misses ({min(show, len(score.misses))} shown of {len(score.misses)}):")
    for miss in score.misses[:show]:
        lines.append(format_miss(miss))
    lines.append("unconfirmed spots:")
    for spot in score.unconfirmed:
        lines.append(f"  {spot.freq_khz:.1f} {spot.call}")
    return lines


def delta_line(a: Score, b: Score) -> str:
    return (
        f"delta: decode recall {b.decode_hit - a.decode_hit:+d}, "
        f"spot recall {b.spot_hit - a.spot_hit:+d}, "
        f"spot precision {b.precision_hit - a.precision_hit:+d}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ours", nargs="+", type=Path, required=True, metavar="PATH")
    parser.add_argument("--cws", type=Path, required=True, metavar="PATH")
    parser.add_argument("--center-khz", type=float, required=True)
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--passband", nargs=2, type=float, metavar=("LO", "HI"))
    parser.add_argument("--tol-khz", type=float, default=DEFAULT_TOL_KHZ)
    parser.add_argument("--show", type=int, default=DEFAULT_SHOW)
    args = parser.parse_args()

    if not 1 <= len(args.ours) <= 2:
        parser.error("--ours takes one or two paths")

    override = (args.passband[0], args.passband[1]) if args.passband else None
    lo, hi = compute_passband(args.center_khz, args.rate, override)

    try:
        cws_spots = parse_cws_tee(args.cws)
        ours_logs = [parse_ours_log(p) for p in args.ours]
    except OSError:
        sys.exit(2)

    scores = [compute_score(log, cws_spots, lo, hi, args.tol_khz) for log in ours_logs]

    for path, score in zip(args.ours, scores, strict=True):
        for line in format_score_block(path.name, score, args.show):
            print(line)

    if len(scores) == 2:
        print(delta_line(scores[0], scores[1]))


if __name__ == "__main__":
    main()
