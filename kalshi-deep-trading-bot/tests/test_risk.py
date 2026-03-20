"""Tests for src.risk.manager module."""

from unittest.mock import patch

import pytest

from src.config import RiskConfig
from src.risk.manager import RiskManager


@pytest.fixture
def default_config():
    return RiskConfig(
        bankroll=1000.0,
        max_bet_amount=100.0,
        max_portfolio_positions=10,
        max_per_event_exposure=200.0,
        max_daily_loss=500.0,
        max_open_positions=20,
        cooldown_after_losses=3,
        kill_switch=False,
        circuit_breaker_threshold=5,
        no_trade_start_hour=-1,
        no_trade_end_hour=-1,
    )


@pytest.fixture
def manager(default_config):
    return RiskManager(default_config)


def _base_state():
    return {
        "open_positions": 0,
        "event_exposure": {},
        "daily_loss": 0.0,
    }


# -- Kill switch --------------------------------------------------------------


def test_kill_switch_blocks_all_trades():
    config = RiskConfig(kill_switch=True)
    mgr = RiskManager(config)
    allowed, reason = mgr.gate_trade("T", "E", 10.0, _base_state())
    assert not allowed
    assert "Kill switch" in reason


def test_kill_switch_off_allows(manager):
    allowed, reason = manager.gate_trade("T", "E", 10.0, _base_state())
    assert allowed
    assert reason == ""


# -- Circuit breaker -----------------------------------------------------------


def test_circuit_breaker_triggers_after_n_failures(manager):
    for _ in range(5):
        manager.record_api_failure()

    allowed, reason = manager.gate_trade("T", "E", 10.0, _base_state())
    assert not allowed
    assert "Circuit breaker" in reason


def test_circuit_breaker_resets_on_success(manager):
    for _ in range(4):
        manager.record_api_failure()
    manager.record_api_success()

    allowed, reason = manager.gate_trade("T", "E", 10.0, _base_state())
    assert allowed


# -- Daily loss limit ----------------------------------------------------------


def test_daily_loss_limit_blocks(manager):
    state = _base_state()
    state["daily_loss"] = 500.0

    allowed, reason = manager.gate_trade("T", "E", 10.0, state)
    assert not allowed
    assert "Daily loss limit" in reason


def test_daily_loss_within_limit(manager):
    state = _base_state()
    state["daily_loss"] = 499.0

    allowed, reason = manager.gate_trade("T", "E", 10.0, state)
    assert allowed


# -- Cooldown ------------------------------------------------------------------


def test_cooldown_triggers_after_consecutive_losses(manager):
    for _ in range(3):
        manager.record_loss(10.0)

    allowed, reason = manager.gate_trade("T", "E", 10.0, _base_state())
    assert not allowed
    assert "Cooldown" in reason


def test_cooldown_resets_on_win(manager):
    for _ in range(2):
        manager.record_loss(10.0)
    manager.record_win()

    allowed, reason = manager.gate_trade("T", "E", 10.0, _base_state())
    assert allowed


# -- No-trade period -----------------------------------------------------------


def test_no_trade_period_blocks_during_window():
    config = RiskConfig(no_trade_start_hour=10, no_trade_end_hour=14)
    mgr = RiskManager(config)

    with patch("src.risk.manager.utc_now") as mock_now, \
         patch("src.risk.manager.is_in_time_window", return_value=True):
        allowed, reason = mgr.gate_trade("T", "E", 10.0, _base_state())
        assert not allowed
        assert "No-trade period" in reason


def test_no_trade_period_disabled_by_default(manager):
    # Default -1 means disabled
    allowed, reason = manager.gate_trade("T", "E", 10.0, _base_state())
    assert allowed


# -- Position count ------------------------------------------------------------


def test_position_count_blocks_at_limit(manager):
    state = _base_state()
    state["open_positions"] = 20

    allowed, reason = manager.gate_trade("T", "E", 10.0, state)
    assert not allowed
    assert "Position limit" in reason


# -- Event exposure ------------------------------------------------------------


def test_event_exposure_blocks_when_exceeded(manager):
    state = _base_state()
    state["event_exposure"] = {"EVT-1": 190.0}

    allowed, reason = manager.gate_trade("T", "EVT-1", 20.0, state)
    assert not allowed
    assert "Event exposure" in reason


def test_event_exposure_allows_within_limit(manager):
    state = _base_state()
    state["event_exposure"] = {"EVT-1": 100.0}

    allowed, reason = manager.gate_trade("T", "EVT-1", 50.0, state)
    assert allowed


# -- Max bet amount ------------------------------------------------------------


def test_max_bet_amount_blocks(manager):
    allowed, reason = manager.gate_trade("T", "E", 150.0, _base_state())
    assert not allowed
    assert "max bet" in reason.lower()


# -- gate_trade returns correct reasons ----------------------------------------


def test_gate_trade_returns_empty_reason_on_allow(manager):
    allowed, reason = manager.gate_trade("T", "E", 10.0, _base_state())
    assert allowed
    assert reason == ""
