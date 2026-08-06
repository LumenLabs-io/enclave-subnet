from __future__ import annotations

from decimal import Decimal

from enclave.environments.base import Environment, Family, InstanceSpec
from enclave.environments.families.archive import ArchiveFamily
from enclave.environments.prf import AuditKey, derive_seed
from enclave.errors import GeneratorError

__all__ = ["REGISTRY", "build", "build_audited", "families", "get"]

REGISTRY: dict[str, Family] = {
    ArchiveFamily().name: ArchiveFamily(),
}


def families() -> tuple[str, ...]:
    return tuple(sorted(REGISTRY))


def get(name: str) -> Family:
    family = REGISTRY.get(name)
    if family is None:
        raise GeneratorError(f"unknown environment family {name!r}; known: {families()}")
    return family


def instance_spec(
    family: str,
    round_seed: str,
    ordinal: int,
    parameters: dict[str, object] | None = None,
) -> InstanceSpec:
    instance_id = f"{family}-{ordinal:04d}"
    return InstanceSpec(
        instance_id=instance_id,
        family=family,
        seed=derive_seed(round_seed, family, instance_id),
        parameters=parameters or {},
    )


def build(spec: InstanceSpec) -> Environment:
    return get(spec.family).generate(spec)


def build_audited(spec: InstanceSpec, key: AuditKey, round_seed: str) -> Environment:
    transform_seed = key.transform_seed(round_seed, spec.instance_id)
    return get(spec.family).audited(spec, transform_seed)


def is_audited(spec: InstanceSpec, key: AuditKey, round_seed: str, rate: Decimal) -> bool:
    return key.selects(round_seed, spec.instance_id, rate)
