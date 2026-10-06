"""Repeat-evidence spot rule (spot_rule.py). Text cases are decodes from the 2026-09-23 replays."""

from spot_rule import REPUTATION_S, SPOT_HOLD_S, RepeatSpotRule, runner_calls

SCP = {"K0TQ", "WX7V", "W1AW", "VE7KW", "K3JT", "K1ABC", "K1AJ", "AA3B", "NA2U"}


def _tier2(call: str) -> int:
    del call
    return 2


def _rule() -> RepeatSpotRule:
    return RepeatSpotRule(SCP, _tier2)


def test_call_right_after_keyword() -> None:
    assert runner_calls("CQ CWT K0TQ") == ["K0TQ"]
    assert runner_calls("TU CWT NA2U") == ["NA2U"]


def test_words_between_keyword_and_call() -> None:
    assert runner_calls("CQ POTA DE WX7V WX7V K") == ["WX7V"]
    assert runner_calls("CQ CQ CQ DE W1AW") == ["W1AW"]


def test_merged_keyword_is_split() -> None:
    assert runner_calls("CQK1ABC K1ABC") == ["K1ABC"]


def test_caller_sent_the_exchange_does_not_count() -> None:
    assert runner_calls("TEST AA3B FRED 2455") == []
    assert runner_calls("CWT VE7KW CWT K3JT K3JT KEITS 2182") == ["VE7KW"]


def test_no_keyword_no_runner() -> None:
    assert runner_calls("DE K7GUD NICE MEET U RICK") == []


def test_spot_after_tier_windows() -> None:
    r = _rule()
    assert r.feed(r.window_id(1, 0, 0.0), 14030.0, "CQ CWT K0TQ", 0.0) == []
    assert r.feed(r.window_id(1, 1, 60.0), 14030.0, "CQ CWT K0TQ", 60.0) == [("K0TQ", 14030.0)]


def test_both_lpf_paths_of_one_window_count_once() -> None:
    r = _rule()
    w = r.window_id(1, 0, 0.0)
    assert r.feed(w, 14030.0, "CQ CWT K0TQ", 0.0) == []
    assert r.feed(r.window_id(1, 0, 0.0), 14030.0, "CQ CWT K0TQ", 0.0) == []


def test_not_in_scp_never_spots() -> None:
    r = _rule()
    for k in range(5):
        assert r.feed(r.window_id(1, k, k * 60.0), 14030.0, "CQ K9ZZZ", k * 60.0) == []


def test_one_spot_per_hold_unless_moved() -> None:
    r = _rule()
    r.feed(r.window_id(1, 0, 0.0), 14030.0, "CQ K0TQ", 0.0)
    assert r.feed(r.window_id(1, 1, 60.0), 14030.0, "CQ K0TQ", 60.0) == [("K0TQ", 14030.0)]
    assert r.feed(r.window_id(1, 2, 120.0), 14030.0, "CQ K0TQ", 120.0) == []
    assert r.feed(r.window_id(2, 0, 180.0), 14035.0, "CQ K0TQ", 180.0) == [("K0TQ", 14035.0)]
    t = 180.0 + SPOT_HOLD_S
    assert r.feed(r.window_id(2, 1, t), 14035.0, "CQ K0TQ", t) == [("K0TQ", 14035.0)]


def test_near_miss_copy_counts_toward_real_call() -> None:
    r = _rule()
    for k in range(4):
        r.feed(r.window_id(1, k, k * 60.0), 14041.1, "K1AJ 599 K1AJ", k * 60.0)
    r.feed(r.window_id(1, 4, 240.0), 14041.1, "CQ K1AA", 240.0)
    assert r.feed(r.window_id(1, 5, 300.0), 14041.1, "CQ K1AJ", 300.0) == [("K1AJ", 14041.1)]


def test_old_windows_are_forgotten() -> None:
    r = _rule()
    r.feed(r.window_id(1, 0, 0.0), 14030.0, "CQ K0TQ", 0.0)
    t = REPUTATION_S + 120.0
    assert r.feed(r.window_id(1, 1, t), 14030.0, "CQ K0TQ", t) == []


def test_spot_rule_key_values() -> None:
    import pytest

    import sparkgap

    try:
        assert sparkgap._select_spot_rule("repeat") is True
        assert sparkgap._select_spot_rule("off") is False
        with pytest.raises(ValueError, match="spot_rule"):
            sparkgap._select_spot_rule("bogus")
    finally:
        sparkgap._select_spot_rule("off")


def test_tracker_spots_window_records() -> None:
    import sparkgap

    tracker = sparkgap.SpotTracker(set(SCP), set())
    # spot_rule is None-typed in sparkgap.py (no hints in Fred's code), so set it via __dict__
    tracker.__dict__["spot_rule"] = RepeatSpotRule(tracker.valid_calls, _tier2)

    def window(key: int) -> sparkgap.SpotIntent:
        return sparkgap.SpotIntent(call="", freq_khz=14030.0, snr_db=12.0, wpm=28, is_runner=True,
                                   window_id=key, bin_id=7, window_text="CQ CWT K0TQ")

    assert tracker.process_intent(window(1)) == []
    assert tracker.process_intent(window(1)) == []
    spots = tracker.process_intent(window(2))
    assert [(s["call"], s["freq_khz"], s["method"]) for s in spots] == [("K0TQ", 14030.0, "repeat")]


def test_scp_copy_outnumbered_two_to_one_counts_as_the_real_call() -> None:
    r = RepeatSpotRule(SCP | {"K1AA"}, _tier2)
    for k in range(4):
        r.feed(r.window_id(1, k, k * 60.0), 14041.1, "CQ K1AJ", k * 60.0)
    assert r.feed(r.window_id(1, 4, 240.0), 14041.1, "CQ K1AA", 240.0) == []


def test_scp_call_not_outnumbered_spots_itself() -> None:
    r = RepeatSpotRule(SCP | {"K1AA"}, _tier2)
    r.feed(r.window_id(1, 0, 0.0), 14041.1, "K1AJ 599", 0.0)
    r.feed(r.window_id(1, 1, 60.0), 14041.1, "CQ K1AA", 60.0)
    assert r.feed(r.window_id(1, 2, 120.0), 14041.1, "CQ K1AA", 120.0) == [("K1AA", 14041.1)]


def test_copy_on_another_frequency_does_not_count() -> None:
    r = _rule()
    for k in range(4):
        r.feed(r.window_id(1, k, k * 60.0), 14041.1, "K1AJ 599 K1AJ", k * 60.0)
    r.feed(r.window_id(2, 0, 240.0), 14041.1, "CQ K1AA", 240.0)
    assert r.feed(r.window_id(3, 0, 300.0), 14035.0, "CQ K1AA", 300.0) == []


def test_tracker_drops_blacklisted_and_overspeed() -> None:
    import sparkgap

    tracker = sparkgap.SpotTracker(set(SCP), {"K0TQ"})
    # spot_rule is None-typed in sparkgap.py (no hints in Fred's code), so set it via __dict__
    tracker.__dict__["spot_rule"] = RepeatSpotRule(tracker.valid_calls, _tier2)

    def window(key: int, text: str, wpm: int = 28) -> sparkgap.SpotIntent:
        return sparkgap.SpotIntent(call="", freq_khz=14030.0, snr_db=12.0, wpm=wpm, is_runner=True,
                                   window_id=key, bin_id=7, window_text=text)

    assert tracker.process_intent(window(1, "CQ K0TQ")) == []
    assert tracker.process_intent(window(2, "CQ K0TQ")) == []
    assert tracker.process_intent(window(3, "CQ W1AW", wpm=55)) == []
    assert tracker.process_intent(window(4, "CQ W1AW", wpm=55)) == []
