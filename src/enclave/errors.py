from __future__ import annotations

__all__ = [
    "AuditError",
    "ChainError",
    "ConfigError",
    "EnclaveError",
    "EnvironmentError_",
    "GeneratorError",
    "GraderError",
    "GraderTamperedError",
    "InfrastructureFault",
    "IsolationError",
    "LedgerError",
    "MeteringError",
    "PricingError",
    "ProtocolError",
    "RelayError",
    "SandboxError",
    "ScoringError",
    "WeightPublicationError",
]


class EnclaveError(Exception):
    pass


class ConfigError(EnclaveError):
    pass


class ProtocolError(EnclaveError):
    pass


class RelayError(EnclaveError):
    pass


class PricingError(RelayError):
    pass


class MeteringError(RelayError):
    pass


class SandboxError(EnclaveError):
    pass


class IsolationError(SandboxError):
    pass


class EnvironmentError_(EnclaveError):
    pass


class GeneratorError(EnvironmentError_):
    pass


class GraderError(EnvironmentError_):
    pass


class GraderTamperedError(GraderError):
    pass


class AuditError(EnclaveError):
    pass


class ScoringError(EnclaveError):
    pass


class LedgerError(EnclaveError):
    pass


class ChainError(EnclaveError):
    pass


class WeightPublicationError(ChainError):
    pass


class InfrastructureFault(EnclaveError):
    pass
