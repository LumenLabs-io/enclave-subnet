from __future__ import annotations

import json
from decimal import Decimal

from enclave.environments import AuditKey, build, build_audited
from enclave.environments.registry import instance_spec


def solve(environment: object) -> dict[str, str]:
    env = environment
    index, _ = env.act("index", {})  # type: ignore[attr-defined]
    facts: dict[str, str] = {}
    for line in index.splitlines():
        doc_id = line.split("\t")[0]
        body, _ = env.act("read", {"doc_id": doc_id})  # type: ignore[attr-defined]
        for row in body.splitlines():
            if " is recorded as " in row:
                head, value = row.split(" is recorded as ")
                attribute = head.split("the ")[1].split(" of record ")[0]
                entity = head.split(" of record ")[1]
                facts[f"{entity}.{attribute}"] = value.rstrip(".")
    return facts


def test_generation_is_deterministic_under_a_seed() -> None:
    left = build(instance_spec("archive", "round-seed", 1))
    right = build(instance_spec("archive", "round-seed", 1))
    assert left.construction_digest() == right.construction_digest()


def test_a_different_round_seed_changes_the_instance() -> None:
    left = build(instance_spec("archive", "round-a", 1))
    right = build(instance_spec("archive", "round-b", 1))
    assert left.construction_digest() != right.construction_digest()


def test_reading_every_document_solves_and_partial_recall_does_not() -> None:
    env = build(instance_spec("archive", "round-seed", 2))
    facts = solve(env)
    assert env.grade(json.dumps(facts)).solved
    partial = dict(list(facts.items())[:1])
    assert not env.grade(json.dumps(partial)).solved


def test_the_audited_variant_defeats_a_memoriser() -> None:
    spec = instance_spec("archive", "round-seed", 3)
    key = AuditKey(b"k" * 32)
    original = build(spec)
    audited = build_audited(spec, key, "round-seed")
    assert not audited.grade(json.dumps(solve(original))).solved
    assert audited.grade(json.dumps(solve(audited))).solved


def test_audit_selection_is_stable_and_committed() -> None:
    key = AuditKey(b"k" * 32)
    rate = Decimal("0.05")
    first = key.selects("round-seed", "archive-0001", rate)
    assert first == key.selects("round-seed", "archive-0001", rate)
    assert key.verify_commitment(key.commitment())
