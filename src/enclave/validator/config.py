from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from enclave.errors import ConfigError

__all__ = ["ValidatorSettings"]


class ValidatorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ENCLAVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    netuid: int = Field(description="Subnet identifier this validator serves")
    wallet_name: str = Field(default="validator")
    wallet_hotkey: str = Field(default="default")
    chain_endpoint: str = Field(default="wss://entrypoint-finney.opentensor.ai:443")

    ledger_root: Path = Field(default=Path("./state/ledger"))
    socket_root: Path = Field(default=Path("./state/sockets"))
    price_snapshot: Path = Field(default=Path("./state/prices.json"))

    families: tuple[str, ...] = Field(default=("archive",))
    instances_per_family: int = Field(default=24, ge=1, le=4096)
    default_model: str = Field(default="")

    provider_base_url: str = Field(default="")
    provider_api_key: str = Field(default="", repr=False)

    container_cpus: Decimal = Field(default=Decimal("2.0"))
    container_memory_gib: int = Field(default=4, ge=1, le=256)
    container_wall_clock_seconds: int = Field(default=900, ge=30, le=86_400)

    dry_run: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    @field_validator("families")
    @classmethod
    def _families_not_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ConfigError("at least one environment family must be enabled")
        return value

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @property
    def memory_bytes(self) -> int:
        return self.container_memory_gib * 1024**3

    def assert_ready(self) -> None:
        problems: list[str] = []
        if not self.default_model:
            problems.append("ENCLAVE_DEFAULT_MODEL must name a model priced by the snapshot")
        if not self.dry_run and not self.provider_api_key:
            problems.append("ENCLAVE_PROVIDER_API_KEY is required unless ENCLAVE_DRY_RUN is set")
        if not self.price_snapshot.exists():
            problems.append(f"price snapshot {self.price_snapshot} does not exist")
        if problems:
            raise ConfigError(
                "validator preflight failed:\n  - " + "\n  - ".join(problems)
            )
