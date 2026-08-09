from __future__ import annotations

from decimal import Decimal
from typing import Final

NETUID: Final = 92
SUBTENSOR_NETWORK: Final = "finney"

# The control plane and the key that signs its directives are network identity, not
# operator configuration. They are compiled in deliberately.
#
# A directive decides the prices, the default model, the families, and every scoring
# parameter, so a validator that could point itself at another control plane, or trust
# another signing key, could grant itself arbitrary network parameters and still call
# itself an Enclave validator. Making these settings would put that one env var edit
# away. Changing them now means shipping a fork, which is visible and which diverges
# from consensus rather than quietly redefining it.
CONTROL_PLANE_URL: Final = "https://api.nclv.io"
OWNER_PUBLIC_KEY: Final = "5F4pTG5AzJwVoRUw97qPCVYAXUPthKTRoKLtKCsVbpccVKkg"

# Used only by a validator that has never held a directive and has never scored a round,
# which is the state before the first revision is published. Any directive, live or
# cached, overrides it, so this address stops being consulted the moment one exists.
FALLBACK_RESERVED_HOTKEY: Final = "5DP4WFrDE57oxqwHDPidMBiB6Ucb8LcELKp7QxhoCpVZmBAF"

RAO_PER_TAO: Final = 1_000_000_000
UPLOAD_FEE_RAO: Final = 35_000_000

PROTOCOL_VERSION: Final = 1
MECHANISM_VERSION: Final = 1
ROUND_SCHEMA_VERSION: Final = 1
RELAY_PROTOCOL_VERSION: Final = 1
PRICE_SNAPSHOT_SCHEMA_VERSION: Final = 1

DOMAIN_SEPARATOR: Final = "enclave"
DIGEST_DOMAIN_ROUND: Final = f"{DOMAIN_SEPARATOR}.round.v1"
DIGEST_DOMAIN_INSTANCE: Final = f"{DOMAIN_SEPARATOR}.instance.v1"
DIGEST_DOMAIN_TRANSCRIPT: Final = f"{DOMAIN_SEPARATOR}.transcript.v1"
DIGEST_DOMAIN_PRICE_SNAPSHOT: Final = f"{DOMAIN_SEPARATOR}.price-snapshot.v1"
DIGEST_DOMAIN_AUDIT_SELECTION: Final = f"{DOMAIN_SEPARATOR}.audit.selection-key.v1"
DIGEST_DOMAIN_AUDIT_TRANSFORM: Final = f"{DOMAIN_SEPARATOR}.audit.transform-key.v1"
DIGEST_DOMAIN_AUDIT_SECRET: Final = f"{DOMAIN_SEPARATOR}.audit.secret-commitment.v1"

SCORING_PRECISION: Final = 96
MONEY_QUANTUM: Final = Decimal("0.00000001")
YIELD_QUANTUM: Final = Decimal("0.000000000001")
WEIGHT_PPM_TOTAL: Final = 1_000_000

DENOMINATOR_FLOOR: Final = Decimal("0.005")
FAILURE_PENALTY: Final = Decimal("0.25")
SPEND_CAP: Final = Decimal("2.00")
WINSORIZE_QUANTILE: Final = Decimal("0.95")

PAID_RANKS: Final = 12
TOP_SHARE: Final = Decimal("0.25")

AUDIT_RATE: Final = Decimal("0.05")
AUDIT_SECRET_MIN_BYTES: Final = 32

MINER_EMISSION_SHARE: Final = Decimal("0.41")

MIN_TIE_MARGIN: Final = Decimal("0.005")
NOISE_MARGIN_MULTIPLIER: Final = Decimal("2.0")
MAX_DECIDABLE_NOISE: Final = Decimal("0.10")

MAX_ONCHAIN_PAYLOAD_BYTES: Final = 1024
SUBMISSION_INTERVAL_SECONDS: Final = 86_400
