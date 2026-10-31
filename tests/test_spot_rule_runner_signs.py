"""Runner signs (spot_rule_runner_signs, cdub89/SparkGap#37). Texts are lake2's 40m decodes of
KH8A on 7002.0, 2026-10-31, after the move to the 40m antenna: split DX that never sends CQ."""

from spot_rule import RepeatSpotRule, runner_signs_of

KH8A_WINDOWS = (
    "JH7ME 5NN OH7ME 5NN KH8A UP E",
    "KG5M 5NE E TU UP E E KH8A",
    "E 3I1EIX 5NN TU UP KH8A UP",
    "KH8A I P AS VDE 5NN UA4C 5NN E E",
    "UP E E KH8A UP",
)
FREQ = 7002.0
WINDOW_S = 30.0


def _tier2(call: str) -> int:
    del call
    return 2


def _feed(rule: RepeatSpotRule, texts: tuple[str, ...],
          freq: float = FREQ) -> list[tuple[str, float]]:
    spots: list[tuple[str, float]] = []
    for k, text in enumerate(texts):
        now = k * WINDOW_S
        spots += rule.feed(rule.window_id(1, k, now), freq, text, now)
    return spots


def test_runner_and_worked_stations_of_one_window() -> None:
    assert runner_signs_of("JH7ME 5NN OH7ME 5NN KH8A UP E") == ({"KH8A"}, {"JH7ME", "OH7ME"})
    assert runner_signs_of("E 3I1EIX 5NN TU UP KH8A UP") == ({"KH8A"}, {"3I1EIX"})
    assert runner_signs_of("TU KH8A") == ({"KH8A"}, set())


def test_no_runner_sign_without_up_or_tu_before_the_call() -> None:
    assert runner_signs_of("KG5M 5NE E TU UP E E KH8A") == (set(), set())
    assert runner_signs_of("CQ CWT K0TQ") == (set(), set())


def test_split_dx_that_never_sends_cq_is_spotted_with_runner_signs_only() -> None:
    assert _feed(RepeatSpotRule(_tier2, lockon=True, cq_groups=True), KH8A_WINDOWS) == []
    rule = RepeatSpotRule(_tier2, lockon=True, cq_groups=True, runner_signs=True)
    spots = _feed(rule, KH8A_WINDOWS)
    assert spots == [("KH8A", FREQ)]


def test_one_window_of_runner_signs_is_not_enough() -> None:
    rule = RepeatSpotRule(_tier2, runner_signs=True)
    assert _feed(rule, ("JH7ME 5NN OH7ME 5NN KH8A UP E", "E E T", "E I")) == []


def test_a_station_sent_a_report_is_never_the_runner() -> None:
    texts = ("JH7ME 5NN TU UP KH8A UP", "TU JH7ME", "TU JH7ME", "TU JH7ME")
    spots = _feed(RepeatSpotRule(_tier2, runner_signs=True), texts)
    assert all(call != "JH7ME" for call, _ in spots)


def test_runner_signs_at_another_frequency_do_not_add_up() -> None:
    rule = RepeatSpotRule(_tier2, runner_signs=True)
    spots = rule.feed(rule.window_id(1, 0, 0.0), 7002.0, "KH8A UP", 0.0)
    spots += rule.feed(rule.window_id(2, 0, 30.0), 7010.0, "KH8A UP", 30.0)
    spots += rule.feed(rule.window_id(3, 0, 60.0), 7020.0, "KH8A UP", 60.0)
    assert spots == []


def test_cq_runners_spot_as_before() -> None:
    texts = ("CQ CWT K0TQ", "TU K0TQ UP", "CQ CWT K0TQ")
    off = _feed(RepeatSpotRule(_tier2, lockon=True, cq_groups=True), texts, 14030.0)
    rule = RepeatSpotRule(_tier2, lockon=True, cq_groups=True, runner_signs=True)
    on = _feed(rule, texts, 14030.0)
    assert on == off == [("K0TQ", 14030.0)]
