from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from enclave.constants import DIGEST_DOMAIN_INSTANCE
from enclave.environments.base import ActionSpec, Grade, InstanceSpec, normalise_answer
from enclave.environments.prf import SeededRandom
from enclave.errors import GeneratorError, GraderError

__all__ = ["ArchiveEnvironment", "ArchiveFamily"]

FAMILY_NAME: Final = "archive"

_ENTITY_NOUNS: Final = (
    "consignment", "manifest", "ledger", "dossier", "charter",
    "registry", "docket", "warrant", "affidavit", "codicil",
)
_ATTRIBUTES: Final = ("custodian", "clearance", "origin", "seal")
_SYLLABLES: Final = (
    "ka", "ro", "mi", "tev", "sul", "nar", "quo", "bel", "dax", "fen",
    "ith", "vor", "zan", "lum", "pha", "gri", "osk", "tyr", "wen", "jal",
)
_FILLER: Final = (
    "The clerk recorded the transfer without incident.",
    "A duplicate was filed with the standing committee.",
    "Subsequent revisions were entered in the margin.",
    "The entry was countersigned before the close of business.",
    "No objection was raised during the review period.",
    "The record was reconciled against the quarterly index.",
    "Attachments were sealed pending further instruction.",
    "The disposition remains provisional until ratified.",
)

_DEFAULT_ENTITIES: Final = 6
_DEFAULT_DOCUMENTS: Final = 14
_DEFAULT_GRADED: Final = 5
_FILLER_PER_DOCUMENT: Final = 6


def _token(rng: SeededRandom, syllables: int = 3) -> str:
    return "".join(rng.choice(list(_SYLLABLES)) for _ in range(syllables))


@dataclass(frozen=True, slots=True)
class _Fact:
    entity: str
    attribute: str
    value: str

    @property
    def key(self) -> str:
        return f"{self.entity}.{self.attribute}"


@dataclass(frozen=True, slots=True)
class _Document:
    doc_id: str
    title: str
    body: str


@dataclass(slots=True)
class ArchiveEnvironment:
    _instance_id: str
    _documents: dict[str, _Document]
    _order: tuple[str, ...]
    _facts: dict[str, _Fact]
    _graded_keys: tuple[str, ...]
    _index_by_term: dict[str, tuple[str, ...]]
    _reads: list[str] = field(default_factory=list)

    @property
    def family(self) -> str:
        return FAMILY_NAME

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def documents_read(self) -> tuple[str, ...]:
        return tuple(self._reads)

    def briefing(self) -> str:
        entities = sorted({fact.entity for fact in self._facts.values()})
        return (
            "You have access to a sealed archive of documents. Every document must be "
            "read through the act channel; nothing is provided up front.\n\n"
            f"Entities of interest: {', '.join(entities)}.\n"
            f"Attributes to recover for each entity: {', '.join(_ATTRIBUTES)}.\n\n"
            "Submit a JSON object mapping \"entity.attribute\" to its recovered value, "
            "covering every entity and every attribute. A subset of the pairs is graded, "
            "and which subset is chosen only after your run completes."
        )

    def actions(self) -> Sequence[ActionSpec]:
        return (
            ActionSpec("index", (), "List every document identifier and title."),
            ActionSpec("search", ("term",), "Return identifiers of documents containing a term."),
            ActionSpec("read", ("doc_id",), "Return the full text of one document."),
        )

    def act(self, action: str, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        if action == "index":
            listing = "\n".join(
                f"{doc_id}\t{self._documents[doc_id].title}" for doc_id in self._order
            )
            return listing, False
        if action == "search":
            term = str(arguments.get("term", "")).strip().casefold()
            if not term:
                raise GeneratorError("search requires a non-empty term")
            hits = self._index_by_term.get(term, ())
            body = "\n".join(hits) if hits else "no documents matched"
            return body, False
        if action == "read":
            doc_id = str(arguments.get("doc_id", "")).strip()
            document = self._documents.get(doc_id)
            if document is None:
                raise GeneratorError(f"no such document {doc_id!r}")
            self._reads.append(doc_id)
            return f"{document.title}\n\n{document.body}", False
        raise GeneratorError(f"unsupported action {action!r}")

    def grade(self, answer: str) -> Grade:
        try:
            parsed = json.loads(answer)
        except json.JSONDecodeError as exc:
            return Grade(solved=False, detail=f"answer is not valid JSON: {exc}")
        if not isinstance(parsed, dict):
            return Grade(solved=False, detail="answer must be a JSON object")

        missing: list[str] = []
        wrong: list[str] = []
        for key in self._graded_keys:
            fact = self._facts.get(key)
            if fact is None:
                raise GraderError(f"graded key {key} is absent from the construction state")
            supplied = parsed.get(key)
            if supplied is None:
                missing.append(key)
            elif normalise_answer(str(supplied)) != normalise_answer(fact.value):
                wrong.append(key)

        if missing or wrong:
            return Grade(
                solved=False,
                detail=f"missing={sorted(missing)} incorrect={sorted(wrong)}",
                expected=json.dumps(
                    {k: self._facts[k].value for k in self._graded_keys}, sort_keys=True
                ),
            )
        return Grade(solved=True, detail=f"all {len(self._graded_keys)} graded pairs correct")

    def construction_digest(self) -> str:
        payload = json.dumps(
            {
                "instance_id": self._instance_id,
                "facts": {k: v.value for k, v in sorted(self._facts.items())},
                "graded": list(self._graded_keys),
                "documents": {d: self._documents[d].body for d in sorted(self._documents)},
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(f"{DIGEST_DOMAIN_INSTANCE}\0{payload}".encode()).hexdigest()


class ArchiveFamily:
    @property
    def name(self) -> str:
        return FAMILY_NAME

    def generate(self, spec: InstanceSpec) -> ArchiveEnvironment:
        return self._build(spec, value_seed=spec.seed)

    def audited(self, spec: InstanceSpec, transform_seed: str) -> ArchiveEnvironment:
        return self._build(spec, value_seed=transform_seed)

    def _build(self, spec: InstanceSpec, value_seed: str) -> ArchiveEnvironment:
        params = spec.parameters
        entities_count = int(params.get("entities", _DEFAULT_ENTITIES))
        documents_count = int(params.get("documents", _DEFAULT_DOCUMENTS))
        graded_count = int(params.get("graded", _DEFAULT_GRADED))

        if entities_count < 1 or documents_count < 1:
            raise GeneratorError("archive requires at least one entity and one document")

        structure = SeededRandom(f"{spec.seed}\0structure")
        values = SeededRandom(f"{value_seed}\0values")
        placement = SeededRandom(f"{spec.seed}\0placement")
        selection = SeededRandom(f"{value_seed}\0selection")

        entities = [
            f"{structure.choice(list(_ENTITY_NOUNS))}-{_token(structure, 2)}"
            for _ in range(entities_count)
        ]
        entities = sorted(set(entities))
        while len(entities) < entities_count:
            entities.append(f"{structure.choice(list(_ENTITY_NOUNS))}-{_token(structure, 3)}")
            entities = sorted(set(entities))

        facts: dict[str, _Fact] = {}
        for entity in entities:
            for attribute in _ATTRIBUTES:
                fact = _Fact(entity=entity, attribute=attribute, value=_token(values, 3))
                facts[fact.key] = fact

        if graded_count > len(facts):
            raise GeneratorError("graded count exceeds the number of planted facts")
        graded_keys = tuple(sorted(selection.sample(sorted(facts), graded_count)))

        buckets: dict[int, list[str]] = {index: [] for index in range(documents_count)}
        for key in sorted(facts):
            buckets[placement.below(documents_count)].append(key)

        documents: dict[str, _Document] = {}
        for index in range(documents_count):
            doc_id = f"doc-{index:03d}"
            lines: list[str] = []
            for key in buckets[index]:
                fact = facts[key]
                lines.append(
                    f"Entry {_token(placement, 2)}: the {fact.attribute} of record "
                    f"{fact.entity} is recorded as {fact.value}."
                )
            for _ in range(_FILLER_PER_DOCUMENT):
                lines.append(placement.choice(list(_FILLER)))
            body = "\n".join(placement.shuffled(lines))
            documents[doc_id] = _Document(
                doc_id=doc_id,
                title=f"Folio {_token(placement, 2)} of the standing archive",
                body=body,
            )

        order = tuple(placement.shuffled(sorted(documents)))

        index_by_term: dict[str, list[str]] = {}
        for doc_id in sorted(documents):
            haystack = f"{documents[doc_id].title}\n{documents[doc_id].body}".casefold()
            for entity in entities:
                if entity.casefold() in haystack:
                    index_by_term.setdefault(entity.casefold(), []).append(doc_id)
            for attribute in _ATTRIBUTES:
                if attribute in haystack:
                    index_by_term.setdefault(attribute, []).append(doc_id)

        return ArchiveEnvironment(
            _instance_id=spec.instance_id,
            _documents=documents,
            _order=order,
            _facts=facts,
            _graded_keys=graded_keys,
            _index_by_term={k: tuple(v) for k, v in index_by_term.items()},
        )
