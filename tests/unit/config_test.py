"""Unit tests for src/config.py — Settings validation and computed fields."""

from __future__ import annotations

import ipaddress

import pytest

# Import the real Settings class (not the singleton) to instantiate freely
from src.config import Settings


@pytest.mark.unit
class TestIsIpAllowed:
    """Settings.is_ip_allowed() — CIDR membership checks."""

    def _make(self, allowed_ips: str) -> Settings:
        # Bypass .env by constructing directly with _env_file=None
        return Settings(
            _env_file=None,  # type: ignore[call-arg]
            allowed_ips=allowed_ips,
            environment="production",
        )

    # --- Arrange shared subnet --------------------------------------------------

    def test_ip_inside_cidr_is_allowed(self):
        # Arrange
        cfg = self._make("192.168.1.0/24")
        # Act
        result = cfg.is_ip_allowed("192.168.1.50")
        # Assert
        assert result is True

    def test_ip_outside_cidr_is_denied(self):
        cfg = self._make("192.168.1.0/24")
        assert cfg.is_ip_allowed("10.0.0.1") is False

    def test_exact_host_entry_is_allowed(self):
        cfg = self._make("203.0.113.42")
        assert cfg.is_ip_allowed("203.0.113.42") is True

    def test_exact_host_entry_mismatch_denied(self):
        cfg = self._make("203.0.113.42")
        assert cfg.is_ip_allowed("203.0.113.43") is False

    def test_multiple_cidrs_comma_separated(self):
        cfg = self._make("10.8.0.0/24,172.16.0.0/16")
        assert cfg.is_ip_allowed("10.8.0.1") is True
        assert cfg.is_ip_allowed("172.16.5.99") is True
        assert cfg.is_ip_allowed("1.2.3.4") is False

    def test_invalid_ip_string_returns_false(self):
        cfg = self._make("10.0.0.0/8")
        assert cfg.is_ip_allowed("not-an-ip") is False

    def test_ipv6_loopback_matched(self):
        cfg = self._make("::1/128")
        assert cfg.is_ip_allowed("::1") is True

    def test_empty_allowed_ips_denies_everything(self):
        cfg = self._make("")
        assert cfg.is_ip_allowed("1.2.3.4") is False

    def test_malformed_cidr_entry_is_skipped(self):
        # Bad CIDR entry should be ignored; valid entry still works
        cfg = self._make("bad-entry,10.0.0.0/8")
        assert cfg.is_ip_allowed("10.1.2.3") is True


@pytest.mark.unit
class TestAllowedNetworks:
    """Settings.allowed_networks — parsed IPv4/IPv6Network objects."""

    def test_returns_list_of_ip_networks(self):
        cfg = Settings(
            _env_file=None,  # type: ignore[call-arg]
            allowed_ips="10.0.0.0/8,192.168.0.0/16",
            environment="production",
        )
        nets = cfg.allowed_networks
        assert len(nets) == 2
        assert all(isinstance(n, (ipaddress.IPv4Network, ipaddress.IPv6Network)) for n in nets)

    def test_empty_string_returns_empty_list(self):
        cfg = Settings(
            _env_file=None,  # type: ignore[call-arg]
            allowed_ips="",
            environment="production",
        )
        assert cfg.allowed_networks == []


@pytest.mark.unit
class TestSettingsProperties:
    """is_development / is_production convenience properties."""

    def test_development_flag(self):
        cfg = Settings(_env_file=None, environment="development")  # type: ignore[call-arg]
        assert cfg.is_development is True
        assert cfg.is_production is False

    def test_production_flag(self):
        cfg = Settings(_env_file=None, environment="production")  # type: ignore[call-arg]
        assert cfg.is_production is True
        assert cfg.is_development is False

    def test_database_url_format(self):
        cfg = Settings(
            _env_file=None,  # type: ignore[call-arg]
            postgres_user="u",
            postgres_password="p",
            postgres_host="db",
            postgres_port=5432,
            postgres_db="mydb",
        )
        assert cfg.database_url == "postgresql+asyncpg://u:p@db:5432/mydb"

    def test_auto_allowed_symbols_set_empty(self):
        cfg = Settings(_env_file=None, auto_allowed_symbols="")  # type: ignore[call-arg]
        assert cfg.auto_allowed_symbols_set == set()

    def test_auto_allowed_symbols_set_parsed(self):
        cfg = Settings(_env_file=None, auto_allowed_symbols="AAPL, btc, SPY")  # type: ignore[call-arg]
        assert cfg.auto_allowed_symbols_set == {"AAPL", "BTC", "SPY"}
