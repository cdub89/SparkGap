"""Settings the GUI feed reports: values in effect, defaults, behaviour built into the rule."""

from sparkgap import _gui_settings


def test_empty_config_reports_defaults() -> None:
    eff, defaults, built_in = _gui_settings({}, 48_000)
    assert eff["cw_decoder"] == "itila" and eff["telnet_port"] == 7300
    assert eff["enable_ft8"] is True
    assert eff["spot_rule"] == "off" and eff["spot_validation"] == "off"
    assert eff["spot_rule_cq_groups"] is False and eff["rbn_known_days"] is None
    assert eff["flex_daxiq_channel"] is None  # not a Flex config
    assert "telnet_port" in defaults and "sample_rate" not in defaults
    assert set(built_in) == {"spot_rule_cq_groups", "spot_rule_lockon", "spot_rule_cq_qualifiers"}


def test_repeat_rule_with_rbn_validation() -> None:
    cfg = {"spot_rule": "repeat", "spot_validation": "normal", "spot_validation_calls": "rbn",
           "sdr_type": "flex", "spot_rule_split_runs": True}
    eff, defaults, _ = _gui_settings(cfg, 96_000)
    assert eff["spot_rule_lockon"] is True and eff["spot_rule_split_runs"] is True
    assert eff["rbn_known_days"] == 7 and "rbn_known_days" in defaults
    assert eff["flex_daxiq_channel"] == 1 and "flex_daxiq_channel" in defaults


def test_calls_ignored_without_validation() -> None:
    eff, _, _ = _gui_settings({"spot_validation_calls": "rbn"}, 48_000)
    assert eff["spot_validation_calls"] == "off" and eff["rbn_known_days"] is None


def test_validation_off_is_accepted() -> None:
    from sparkgap import _make_spot_rule
    eff, _, _ = _gui_settings({"spot_validation": "off", "spot_validation_calls": "rbn"}, 48_000)
    assert eff["spot_validation"] == "off" and eff["spot_validation_calls"] == "off"
    assert _make_spot_rule({"spot_validation": "off"}, None).threshold is None
