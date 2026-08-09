from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from enclave.constants import CONTROL_PLANE_URL, NETUID, OWNER_PUBLIC_KEY
from enclave.errors import ConfigError

__all__ = ["ValidatorSettings"]


class ValidatorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ENCLAVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    netuid: int = Field(default=NETUID, description="Subnet identifier this validator serves")
    wallet_name: str = Field(default="validator")
    wallet_hotkey: str = Field(default="default")
    chain_endpoint: str = Field(default="wss://entrypoint-finney.opentensor.ai:443")

    # Accepted so that an existing .env still parses, but not honoured. The values the
    # code uses are the compiled constants; anything else here is refused by
    # assert_ready rather than silently overriding network identity.
    control_plane_url: str = Field(default=CONTROL_PLANE_URL)
    owner_public_key: str = Field(default=OWNER_PUBLIC_KEY)

    ledger_root: Path = Field(default=Path("./state/ledger"))
    socket_root: Path = Field(default=Path("./state/sockets"))

    # Prices, the default model, the families, and the instance count are consensus
    # critical: they decide what every yield is divided by. They arrive in the owner
    # signed directive so that no two validators can score the same evidence differently.
    # These remain only as dry run overrides, where nothing is published.
    price_snapshot: Path = Field(default=Path("./state/prices.json"))
    families: tuple[str, ...] = Field(default=("archive",))
    instances_per_family: int = Field(default=24, ge=1, le=4096)
    default_model: str = Field(default="")

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
        if self.control_plane_url != CONTROL_PLANE_URL:
            problems.append(
                f"ENCLAVE_CONTROL_PLANE_URL cannot be overridden. This build talks to "
                f"{CONTROL_PLANE_URL}; remove the line from your .env"
            )
        if self.owner_public_key != OWNER_PUBLIC_KEY:
            problems.append(
                f"ENCLAVE_OWNER_PUBLIC_KEY cannot be overridden. This build trusts "
                f"{OWNER_PUBLIC_KEY}; remove the line from your .env. Trusting another key "
                "would mean accepting network parameters this subnet's owner never signed"
            )
        if problems:
            raise ConfigError(
                "validator preflight failed:\n  - " + "\n  - ".join(problems)
            )
