"""Tests for src.config module."""

import os
import tempfile

import pytest
from pydantic import ValidationError

from src.config import (
    BotConfig,
    HedgeConfig,
    KalshiConfig,
    OctagonConfig,
    OpenAIConfig,
    RiskConfig,
    StrategyConfig,
    _clean_env_value,
)


# -- _clean_env_value --------------------------------------------------------


def test_clean_env_value_strips_comments():
    assert _clean_env_value("100.0  # max bet") == "100.0"


def test_clean_env_value_no_comment():
    assert _clean_env_value("hello") == "hello"


def test_clean_env_value_empty():
    assert _clean_env_value("") == ""


def test_clean_env_value_only_comment():
    assert _clean_env_value("# this is a comment") == ""


# -- KalshiConfig ------------------------------------------------------------

DUMMY_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MhgHcTz6sE2I2yPB\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_kalshi_config_inline_pem():
    config = KalshiConfig(api_key="test_key", private_key=DUMMY_PEM)
    assert config.private_key.startswith("-----BEGIN")
    assert config.use_demo is True
    assert "demo" in config.base_url


def test_kalshi_config_production_url():
    config = KalshiConfig(api_key="test_key", private_key=DUMMY_PEM, use_demo=False)
    assert "elections" in config.base_url


def test_kalshi_config_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(DUMMY_PEM)
        f.flush()
        config = KalshiConfig(api_key="test_key", private_key=f.name)
        assert config.private_key.startswith("-----BEGIN")
    os.unlink(f.name)


def test_kalshi_config_invalid_pem():
    with pytest.raises(ValidationError):
        KalshiConfig(api_key="test_key", private_key="not_a_pem_string")


def test_kalshi_config_missing_file():
    with pytest.raises(ValidationError):
        KalshiConfig(api_key="test_key", private_key="/nonexistent/path.pem")


# -- BotConfig ---------------------------------------------------------------


def test_bot_config_valid():
    config = BotConfig(
        kalshi=KalshiConfig(api_key="k", private_key=DUMMY_PEM),
        octagon=OctagonConfig(api_key="o"),
        openai=OpenAIConfig(api_key="a"),
    )
    assert config.dry_run is True
    assert config.live_trading is False


def test_bot_config_live_requires_no_dry_run():
    with pytest.raises(ValidationError, match="live_trading=True requires dry_run=False"):
        BotConfig(
            kalshi=KalshiConfig(api_key="k", private_key=DUMMY_PEM),
            octagon=OctagonConfig(api_key="o"),
            openai=OpenAIConfig(api_key="a"),
            dry_run=True,
            live_trading=True,
        )


def test_bot_config_live_with_dry_run_false():
    config = BotConfig(
        kalshi=KalshiConfig(api_key="k", private_key=DUMMY_PEM),
        octagon=OctagonConfig(api_key="o"),
        openai=OpenAIConfig(api_key="a"),
        dry_run=False,
        live_trading=True,
    )
    assert config.live_trading is True
    assert config.dry_run is False


# -- Sub-config defaults ------------------------------------------------------


def test_risk_config_defaults():
    config = RiskConfig()
    assert config.bankroll == 1000.0
    assert config.kill_switch is False
    assert config.circuit_breaker_threshold == 5
    assert config.no_trade_start_hour == -1


def test_strategy_config_defaults():
    config = StrategyConfig()
    assert config.z_threshold == 1.5
    assert config.min_edge == 0.05
    assert config.min_volume == 1000
    assert config.max_hours_to_expiry == 168.0


def test_hedge_config_defaults():
    config = HedgeConfig()
    assert config.enable_hedging is True
    assert config.skip_instead_of_hedge is True


def test_octagon_config_default_url():
    config = OctagonConfig(api_key="test")
    assert "octagonagents.com" in config.base_url
