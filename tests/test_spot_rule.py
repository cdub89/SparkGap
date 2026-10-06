"""Repeat-evidence spot rule (spot_rule.py). Text cases are decodes from the 2026-09-23 replays."""

from spot_rule import REPUTATION_S, SPOT_HOLD_S, RepeatSpotRule, runner_calls

SCP = {"K0TQ", "WX7V", "W1AW", "VE7KW", "K3JT", "K1ABC", "K1AJ", "AA3B", "NA2U"}


def _tier2(call: str) -> int:
    del call
    return 2


def _rule() -> RepeatSpotRule:
    return RepeatSpotRule(_tier2)


def test_call_right_after_keyword() -> None:
    assert runner_calls("CQ CWT K0TQ") == ["K0TQ"]
    assert runner_calls("TU CWT NA2U") == []


def test_words_between_keyword_and_call() -> None:
    assert runner_calls("CQ POTA DE WX7V WX7V K") == ["WX7V"]
    assert runner_calls("CQ CQ CQ DE W1AW") == ["W1AW"]


def test_merged_keyword_is_split() -> None:
    assert runner_calls("CQK1ABC K1ABC") == ["K1ABC"]


def test_caller_sent_the_exchange_does_not_count() -> None:
    assert runner_calls("TEST AA3B FRED 2455") == []
    assert runner_calls("TEST VE7KW TEST K3JT K3JT KEITS 2182") == ["VE7KW"]


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


def test_call_not_in_scp_spots_on_repeats() -> None:
    r = _rule()
    assert r.feed(r.window_id(1, 0, 0.0), 14030.0, "CQ K9ZZZ", 0.0) == []
    assert r.feed(r.window_id(1, 1, 60.0), 14030.0, "CQ K9ZZZ", 60.0) == [("K9ZZZ", 14030.0)]


def test_call_not_in_scp_needs_keyword_and_its_tier() -> None:
    r = RepeatSpotRule(lambda _: 3)
    for k in range(4):
        assert r.feed(r.window_id(1, k, k * 60.0), 14030.0, "DE K9ZZZ K9ZZZ", k * 60.0) == []
    assert r.feed(r.window_id(1, 4, 240.0), 14030.0, "CQ K9ZZZ", 240.0) == []
    assert r.feed(r.window_id(1, 5, 300.0), 14030.0, "CQ K9ZZZ", 300.0) == []
    assert r.feed(r.window_id(1, 6, 360.0), 14030.0, "CQ K9ZZZ", 360.0) == [("K9ZZZ", 14030.0)]


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
    tracker.__dict__["spot_rule"] = RepeatSpotRule(_tier2)

    def window(key: int) -> sparkgap.SpotIntent:
        return sparkgap.SpotIntent(call="", freq_khz=14030.0, snr_db=12.0, wpm=28, is_runner=True,
                                   window_id=key, bin_id=7, window_text="CQ CWT K0TQ")

    assert tracker.process_intent(window(1)) == []
    assert tracker.process_intent(window(1)) == []
    spots = tracker.process_intent(window(2))
    assert [(s["call"], s["freq_khz"], s["method"]) for s in spots] == [("K0TQ", 14030.0, "repeat")]


def test_copy_outnumbered_two_to_one_counts_as_the_real_call() -> None:
    r = RepeatSpotRule(_tier2)
    for k in range(4):
        r.feed(r.window_id(1, k, k * 60.0), 14041.1, "CQ K1AJ", k * 60.0)
    assert r.feed(r.window_id(1, 4, 240.0), 14041.1, "CQ K1AA", 240.0) == []


def test_call_not_outnumbered_spots_itself() -> None:
    r = RepeatSpotRule(_tier2)
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
    tracker.__dict__["spot_rule"] = RepeatSpotRule(_tier2)

    def window(key: int, text: str, wpm: int = 28) -> sparkgap.SpotIntent:
        return sparkgap.SpotIntent(call="", freq_khz=14030.0, snr_db=12.0, wpm=wpm, is_runner=True,
                                   window_id=key, bin_id=7, window_text=text)

    assert tracker.process_intent(window(1, "CQ K0TQ")) == []
    assert tracker.process_intent(window(2, "CQ K0TQ")) == []
    assert tracker.process_intent(window(3, "CQ W1AW", wpm=55)) == []
    assert tracker.process_intent(window(4, "CQ W1AW", wpm=55)) == []


def test_truncated_call_counts_toward_the_full_call() -> None:
    r = _rule()
    for k in range(3):
        r.feed(r.window_id(1, k, k * 60.0), 14040.5, "KM7EJE KM7EJE", k * 60.0)
    assert r.feed(r.window_id(1, 3, 180.0), 14040.5, "CQ POTA DE KM7E", 180.0) == []
    spots = r.feed(r.window_id(1, 4, 240.0), 14040.5, "CQ POTA DE KM7E", 240.0)
    assert spots == [("KM7EJE", 14040.5)]


def test_truncated_call_waits_while_a_longer_call_is_heard() -> None:
    r = RepeatSpotRule(lambda c: 4 if c == "WB0RTA" else 2)
    r.feed(r.window_id(1, 0, 0.0), 14013.4, "WB1RTA WB0RTA", 0.0)
    for k in range(1, 4):
        assert r.feed(r.window_id(1, k, k * 60.0), 14013.4, "CQ CQ DE WB0R", k * 60.0) == []


def test_glued_prosign_is_not_a_longer_call() -> None:
    r = _rule()
    for k in range(4):
        r.feed(r.window_id(1, k, k * 60.0), 14069.2, "AB0CDKN", k * 60.0)
    r.feed(r.window_id(1, 4, 240.0), 14069.2, "CQ AB0CD", 240.0)
    assert r.feed(r.window_id(1, 5, 300.0), 14069.2, "CQ AB0CD", 300.0) == [("AB0CD", 14069.2)]


def test_stale_neighbour_does_not_take_a_new_runner() -> None:
    r = _rule()
    for k in range(4):
        r.feed(r.window_id(1, k, k * 60.0), 14030.0, "CQ K1AB", k * 60.0)
    assert r.feed(r.window_id(2, 0, 700.0), 14030.0, "CQ K1AA", 700.0) == []
    assert r.feed(r.window_id(2, 1, 760.0), 14030.0, "CQ K1AA", 760.0) == [("K1AA", 14030.0)]


def test_short_call_heard_more_than_the_longer_one_is_not_merged() -> None:
    r = _rule()
    for k in range(2):
        r.feed(r.window_id(1, k, k * 60.0), 14030.0, "K4NAX 5NN", k * 60.0)
    for k in range(2, 6):
        r.feed(r.window_id(1, k, k * 60.0), 14030.0, "K4N K4N", k * 60.0)
    assert r._dominant("K4N") is None


def test_noise_and_prosign_extensions_are_not_longer_calls() -> None:
    r = _rule()
    for k, text in enumerate(("AB0CDBT", "AB0CDAS", "AB0CDEE", "AB0CDEE")):
        r.feed(r.window_id(1, k, k * 60.0), 14069.2, text, k * 60.0)
    assert r._longer("AB0CD") == set()


def test_equal_longer_candidates_resolve_by_call() -> None:
    r = _rule()
    for k in range(3):
        r.feed(r.window_id(1, k, k * 60.0), 14030.0, "K4NAX K4NZZ", k * 60.0)
    r.feed(r.window_id(1, 3, 180.0), 14030.0, "CQ K4N", 180.0)
    assert r._dominant("K4N") == "K4NAX"


def test_call_after_de_counts_when_cq_came_first_and_it_is_sent_twice() -> None:
    assert runner_calls("CQ SKCC CG EKCC DE K4DH K4DH") == ["K4DH"]
    assert runner_calls("NOT E 3KZE CQ I EE DE VE3N") == []
    assert runner_calls("CQ K4DH DE W1AW W1AW") == ["K4DH"]
    assert runner_calls("TU DE W1AW W1AW") == []
    assert runner_calls("CQ SKCC CG EKCC DEW1AW W1AW") == ["W1AW"]
    assert runner_calls("CQ K1ABC? FOO BAR BAZ DE W1XYZ W1XYZ") == []
    assert runner_calls("CQ K1ABC? FOO BAR BAZ DEW1XYZ W1XYZ") == []
