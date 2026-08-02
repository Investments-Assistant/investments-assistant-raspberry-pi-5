"""Central application configuration via pydantic-settings (reads from .env)."""

from __future__ import annotations

from functools import lru_cache
import ipaddress
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    environment: Literal["development", "production"] = "production"
    log_level: str = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # ── Security ───────────────────────────────────────────────────────────────
    allowed_ips: str = (
        "10.8.0.0/24"  # NOSONAR — private WireGuard VPN subnet; override via ALLOWED_IPS env var
    )
    # Nginx is the only public entry point in the Compose deployment.  Keep
    # proxy headers enabled there, but make the trust decision explicit so a
    # future direct deployment does not accidentally trust user-supplied XFF.
    trust_proxy_headers: bool = True

    # Browser authentication.  Passwords are stored as scrypt hashes generated
    # by scripts/create_auth_hash.py; the clear-text password is never needed by
    # the application process.
    auth_username: str = Field("admin", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.@-]+$")
    auth_password_hash: str = ""
    auth_session_secret: str = ""
    auth_session_ttl_minutes: int = Field(480, ge=5, le=7 * 24 * 60)
    auth_require_login: bool = True
    auth_cookie_secure: bool = True
    max_chat_message_chars: int = Field(8_000, ge=1_000, le=32_000)
    # Fernet key used to encrypt per-user brokerage credentials at rest.
    # Generate with scripts/create_broker_key.py and keep it outside the DB.
    broker_credentials_key: str = ""

    # Optional, separately scoped access for the local MCP bridge.  It is off
    # by default because the browser UI is the primary control surface.
    mcp_enabled: bool = False
    mcp_auth_token: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        networks = []
        for entry in self.allowed_ips.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                pass
        return networks

    def is_ip_allowed(self, ip: str) -> bool:
        """Return True if *ip* matches any configured CIDR / host entry."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.allowed_networks)

    # ── LLM backend (local GGUF via llama.cpp — no external APIs) ─────────────
    # Absolute path to the GGUF model file on disk.
    llm_model_path: str = "/app/models/qwen2.5-7b-instruct-q4_k_m.gguf"
    # Context window in tokens (reduce if you run out of RAM).
    llm_context_size: int = 4096
    # GPU layers to offload: 0 = CPU only (Pi 5 has no GPU), -1 = all to GPU.
    llm_n_gpu_layers: int = 0
    # CPU worker threads for llama.cpp. Pi 5 has 4 Cortex-A76 cores.
    llm_n_threads: int = 4
    # Prompt-processing batch size. Lower this if memory pressure appears.
    llm_n_batch: int = 128

    agent_max_tokens: int = Field(2048, ge=128, le=16_384)
    agent_temperature: float = 0.1
    # With llm_context_size=4096, system prompt (~400 tok) + response (2048 tok)
    # leaves ~1648 tokens for history — roughly 10 messages at ~150 tok each.
    # Set higher when using a larger local context window.
    agent_max_context_messages: int = 15
    agent_max_tool_rounds: int = Field(8, ge=1, le=32)
    agent_max_tool_result_chars: int = Field(12_000, ge=1_000, le=100_000)

    # ── Trading ────────────────────────────────────────────────────────────────
    trading_mode: Literal["recommend", "auto"] = "recommend"
    auto_max_trade_usd: float = Field(500.0, gt=0)
    auto_daily_loss_limit_usd: float = Field(1000.0, gt=0)
    auto_allowed_symbols: str = ""  # empty = all allowed
    auto_allow_market_orders: bool = False
    live_trading_enabled: bool = False
    autonomous_scans_enabled: bool = True
    autonomous_scan_interval_minutes: int = Field(60, ge=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auto_allowed_symbols_set(self) -> set[str]:
        if not self.auto_allowed_symbols.strip():
            return set()
        return {s.strip().upper() for s in self.auto_allowed_symbols.split(",")}

    # ── Database ───────────────────────────────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "investment_assistant"
    postgres_user: str = "ia_user"
    postgres_password: str = "change_me"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Alpaca ─────────────────────────────────────────────────────────────────
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    # ── Interactive Brokers ────────────────────────────────────────────────────
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 1
    ibkr_enabled: bool = False

    # ── Coinbase ───────────────────────────────────────────────────────────────
    coinbase_api_key: str = ""
    coinbase_api_secret: str = ""

    # ── Binance ────────────────────────────────────────────────────────────────
    binance_api_key: str = ""
    binance_secret_key: str = ""
    binance_testnet: bool = True

    # ── News ───────────────────────────────────────────────────────────────────
    newsapi_key: str = ""
    guardian_api_key: str = ""  # free key at https://open-platform.theguardian.com/

    # ── Newsletter email ingestion (IMAP) ──────────────────────────────────────
    # Works with Gmail (enable IMAP + create an App Password if 2FA is on),
    # Outlook, and any standard IMAP server.
    newsletter_imap_server: str = "imap.gmail.com"
    newsletter_imap_port: int = 993
    newsletter_email_user: str = ""  # your email address
    newsletter_email_password: str = ""  # app password, not your main password
    newsletter_sender_filter: str = ""  # only ingest emails FROM this address

    # NewsAPI/Guardian are retained as optional adapters for compatibility, but
    # public RSS and HTML sources are the default and require no API keys.
    # Leave these empty for the strict local/no-news-API deployment.
    news_api_adapters_enabled: bool = False

    # ── Scheduler ─────────────────────────────────────────────────────────────
    market_data_refresh_minutes: int = Field(5, ge=1)
    news_ingestion_minutes: int = Field(60, ge=1)
    weekly_report_day: int = 6  # weekday index: Monday is 0, Sunday is 6
    weekly_report_hour: int = 18
    weekly_report_minute: int = 0

    # ── Reports ────────────────────────────────────────────────────────────────
    reports_dir: str = "/app/reports"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def authentication_ready(self) -> bool:
        """Return whether production browser authentication can be enforced."""
        return bool(self.auth_username and self.auth_password_hash and self.auth_session_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Convenience singleton
settings = get_settings()
