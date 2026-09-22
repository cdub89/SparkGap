import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))

from parity_score import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    OurLog,
    RawDecode,
    Spot,
    compute_score,
    decode_recall,
    parse_cws_tee,
    parse_ours_log,
    token_match,
)


def test_cws_tee_parsing(tmp_path: Path) -> None:
    """Parses freq, base call, cq flag and the optional mode column from a CW Skimmer tee."""
    log = tmp_path / "cws.log"
    log.write_text(
        "01:49:21 DX de WX7V-#: 7061.5 N0RNM CW 13 dB 20 WPM CQ 0149Z\n"
        "01:50:14 DX de WX7V-#: 7048.0 EN5TT CW 24 dB 21 WPM 0150Z\n"
        "01:50:20 DX de WX7V-#: 7050.0 W1AW/9 CW 10 dB 15 WPM CQ 0150Z\n"
        "01:50:25 DX de WX7V-#: 7052.0 K5ABC RTTY 10 dB 0150Z\n"
        "14:06:52 DX de WX7V-#:    14038.0  NZ4N           21 dB  27 WPM                1406Z\n"
        "14:08:26 DX de WX7V-#:    14066.5  VE3IIM         14 dB  22 WPM   CQ           1408Z\n"
    )
    spots = parse_cws_tee(log)
    assert len(spots) == 5
    assert spots[0] == Spot(7061.5, "N0RNM", True)
    assert spots[1] == Spot(7048.0, "EN5TT", False)
    assert spots[2] == Spot(7050.0, "W1AW", True)
    assert spots[3] == Spot(14038.0, "NZ4N", False)
    assert spots[4] == Spot(14066.5, "VE3IIM", True)


def test_our_log_parsing_and_dedupe(tmp_path: Path) -> None:
    """Parses ITILA raw lines and both our-spot shapes, deduping a spot seen in both."""
    log = tmp_path / "ours.log"
    log.write_text(
        "20:49:36 INFO ITILA raw 7037.5 kHz cost=0.32: "
        "'NA ? 5 U SE 5US A D 5 U S TD5 I E T EN'\n"
        "20:50:36 INFO *** SPOT:     7045.0  I8UGP         25 dB  14 WPM  [unverified] ***\n"
        "01:50:36 DX de WX7V-1:     7045.00  I8UGP        CW   25 dB 14 WPM   CQ"
        "                  SG  0150Z\n"
    )
    ours = parse_ours_log(log)
    assert len(ours.raw) == 1
    assert ours.raw[0].freq_khz == 7037.5
    assert "NA ? 5 U SE" in ours.raw[0].text
    assert len(ours.spots) == 1
    assert ours.spots[0].call == "I8UGP"
    assert ours.spots[0].cq is True


def test_passband_excludes_out_of_band_spot() -> None:
    """A CW Skimmer spot outside center +/- rate/4 is excluded from the denominator."""
    lo, hi = 7035.0, 7059.0
    ours = OurLog(raw=[RawDecode(7040.0, "CQ N0RNM K")], spots=[])
    cws_spots = [Spot(7040.0, "N0RNM", False), Spot(7020.0, "OUTBAND", False)]
    score = compute_score(ours, cws_spots, lo, hi, 0.5)
    assert score.decode_total == 1
    assert score.decode_hit == 1


def test_tolerance_window() -> None:
    """A raw bin within 0.5 kHz counts; further away, even for the same call, it does not."""
    spot = Spot(7030.9, "W1AW", False)
    close_raw = [RawDecode(7031.0, "CQ W1AW K")]
    assert decode_recall([spot], close_raw, 0.5) == (1, 1)

    far_raw = [RawDecode(7033.0, "CQ W1AW K")]
    assert decode_recall([spot], far_raw, 0.5) == (0, 1)

    far_spot = Spot(7035.0, "W1AW", False)
    assert decode_recall([far_spot], close_raw, 0.5) == (0, 1)


def test_token_match_whole_word_only() -> None:
    """Token match requires call boundaries, not a substring."""
    assert token_match("KB8UGP", "KB8UG G P") is False
    assert token_match("KB8UGP", "CQ KB8UGP K") is True


def test_metrics_three_spot_fixture() -> None:
    """A three-spot fixture yields expected recall/precision, cq variants and misses."""
    cws_spots = [
        Spot(7040.0, "N0RNM", True),
        Spot(7045.0, "KB8UGP", False),
        Spot(7050.0, "EN5TT", True),
    ]
    raw = [
        RawDecode(7040.0, "CQ N0RNM K"),
        RawDecode(7045.0, "KB8UG G P"),
        RawDecode(7050.0, "CQ EN5TT K"),
    ]
    our_spots = [Spot(7040.0, "N0RNM", True), Spot(7060.0, "ZZ1ZZ", False)]
    ours = OurLog(raw=raw, spots=our_spots)

    score = compute_score(ours, cws_spots, 7000.0, 7100.0, 0.5)

    assert (score.decode_hit, score.decode_total) == (2, 3)
    assert (score.spot_hit, score.spot_total) == (1, 3)
    assert (score.precision_hit, score.precision_total) == (1, 2)
    assert (score.decode_cq_hit, score.decode_cq_total) == (2, 2)
    assert (score.spot_cq_hit, score.spot_cq_total) == (1, 2)

    assert len(score.misses) == 1
    miss = score.misses[0]
    assert miss.call == "KB8UGP"
    assert miss.nearest_text == "KB8UG G P"
