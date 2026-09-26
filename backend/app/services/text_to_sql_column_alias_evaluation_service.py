"""Phase 3.9.18 — Context-aware semantic column alias registry.

**Evaluation layer only.** This module is never imported by
``TextToSQLService`` / ``TextToSQLGenerator`` / ``TextToSQLValidator`` /
``TextToSQLExecutor`` / Prompt / AI Router / AI Orchestrator.

Guarantees (section §六):

```text
pure function | deterministic | no DB | no LLM | no network
```

Problem (section §一)
---------------------

The same business concept may be projected under different column names:

```text
documents.id   ->  id  |  document_id
chunks.id      ->  id  |  chunk_id
COUNT(*)       ->  count  |  total_count  |  total_documents
```

A naive global rule such as ``"id" -> "document_id"`` is **unsafe**, because
``documents.id`` and ``chunks.id`` are different business entities.

Model (section §四)
-------------------

Every alias declaration is bound to a business entity:

```text
semantic_name + entity + source_table + source_column + aliases
```

Alias lookup therefore never crosses entity boundaries.

Matching priority (section §七)
--------------------------------

```text
1. exact column name          -> EXACT_MATCH   (no context required)
2. explicit semantic alias    -> ALIAS_MATCH   (expected IS the canonical
                                                semantic_name of a concept)
3. context-aware alias        -> ALIAS_MATCH   (expected is a bare alias such
                                                as `id` / `count`; the concept
                                                is selected by entity +
                                                aggregate context)
4. otherwise                  -> UNKNOWN_COLUMN
```

Forbidden by design (sections §五 / §七):

```text
fuzzy string matching | Levenshtein | embedding similarity | LLM-as-a-judge
```

Aggregate rule (section §五 / §十三)
------------------------------------

``count`` / ``total_count`` are shared by several entities and are therefore
**never** matched without an entity context. ``total_documents`` is an alias
of the *document* count concept only — it can never satisfy a chunk count
expectation, and vice versa.

Evaluation principle
--------------------

> An ambiguous column resolves to ``UNKNOWN_COLUMN`` instead of being
> guessed. A false positive is worse than an explicit unknown.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "MATCH_EXACT",
    "MATCH_ALIAS",
    "MATCH_NONE",
    "MATCH_KINDS",
    "DIAGNOSTIC_EXACT_MATCH",
    "DIAGNOSTIC_ALIAS_MATCH",
    "DIAGNOSTIC_UNKNOWN_COLUMN",
    "ENTITY_DOCUMENT",
    "ENTITY_CHUNK",
    "ENTITY_INVENTORY_ITEM",
    "AGGREGATE_COUNT",
    "AGGREGATE_NONE",
    "ColumnAliasContext",
    "ColumnSemanticAlias",
    "ColumnAliasResolution",
    "SEMANTIC_COLUMN_ALIASES",
    "SEMANTIC_COLUMN_ALIAS_BY_NAME",
    "CASE_ALIAS_CONTEXTS",
    "NA_CASE_ALIAS_CONTEXTS",
    "alias_context_for_case",
    "resolve_semantic_column",
    "resolve_required_columns",
    "validate_alias_registry",
]


# ============================================================
# Constants
# ============================================================

#: ``matched_by`` value: the expected column name is literally present.
MATCH_EXACT: Final[str] = "exact"
#: ``matched_by`` value: matched through a declared, entity-scoped alias.
MATCH_ALIAS: Final[str] = "alias"
#: ``matched_by`` value: no deterministic match (reported as UNKNOWN).
MATCH_NONE: Final[str] = "none"

MATCH_KINDS: Final[frozenset[str]] = frozenset({
    MATCH_EXACT, MATCH_ALIAS, MATCH_NONE,
})

#: Diagnostic recorded when a required column matched by exact name.
DIAGNOSTIC_EXACT_MATCH: Final[str] = "EXACT_MATCH"
#: Diagnostic recorded when a required column matched by entity-scoped alias.
#: This is NEITHER a warning NOR an error (section §九).
DIAGNOSTIC_ALIAS_MATCH: Final[str] = "ALIAS_MATCH"
#: Diagnostic recorded when no deterministic match exists.
DIAGNOSTIC_UNKNOWN_COLUMN: Final[str] = "UNKNOWN_COLUMN"

#: Business entities known to the evaluation fixture (``t2s_eval``).
ENTITY_DOCUMENT: Final[str] = "document"
ENTITY_CHUNK: Final[str] = "chunk"
ENTITY_INVENTORY_ITEM: Final[str] = "inventory_item"

AGGREGATE_COUNT: Final[str] = "count"
AGGREGATE_NONE: Final[str] = ""


@dataclass(frozen=True)
class ColumnAliasContext:
    """Entity (and optionally aggregate) context for one expected column.

    The context is what makes alias resolution safe: without it, a bare
    ``id`` or ``count`` stays UNKNOWN instead of being guessed.
    """
    entity: str
    aggregate: str | None = None


@dataclass(frozen=True)
class ColumnSemanticAlias:
    """One business concept and the projection names it may appear under.

    ``semantic_name`` is the canonical name. ``aliases`` must contain
    ``semantic_name`` itself and every projection name that provably
    denotes the same business concept **for this entity**.
    """
    semantic_name: str
    entity: str
    source_table: str
    source_column: str
    aliases: frozenset[str]
    aggregate: str | None = None

    def __post_init__(self) -> None:
        if not self.semantic_name:
            raise ValueError("semantic_name must be non-empty")
        if not self.entity:
            raise ValueError("entity must be non-empty")
        if not self.aliases:
            raise ValueError("aliases must be non-empty")
        if self.semantic_name.lower() not in self.aliases:
            raise ValueError(
                f"semantic_name {self.semantic_name!r} must be part of "
                f"its own aliases"
            )

    @property
    def aggregate_kind(self) -> str:
        return self.aggregate or AGGREGATE_NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_name": self.semantic_name,
            "entity": self.entity,
            "source_table": self.source_table,
            "source_column": self.source_column,
            "aggregate": self.aggregate,
            "aliases": sorted(self.aliases),
        }


@dataclass(frozen=True)
class ColumnAliasResolution:
    """Outcome of resolving one expected semantic column."""
    expected_column: str
    actual_column: str | None = None
    semantic_name: str | None = None
    entity: str | None = None
    match_kind: str = MATCH_NONE
    diagnostic: str = DIAGNOSTIC_UNKNOWN_COLUMN
    detail: str = ""

    @property
    def matched(self) -> bool:
        return self.match_kind in (MATCH_EXACT, MATCH_ALIAS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_column": self.expected_column,
            "actual_column": self.actual_column,
            "semantic_name": self.semantic_name,
            "entity": self.entity,
            "matched_by": self.match_kind,
            "diagnostic": self.diagnostic,
            "detail": self.detail,
        }


def _concept(
    semantic_name: str,
    entity: str,
    source_table: str,
    source_column: str,
    aliases: Sequence[str],
    aggregate: str | None = None,
) -> ColumnSemanticAlias:
    return ColumnSemanticAlias(
        semantic_name=semantic_name,
        entity=entity,
        source_table=source_table,
        source_column=source_column,
        aliases=frozenset(str(a).strip().lower() for a in aliases),
        aggregate=aggregate,
    )


# ============================================================
# Registry (entity-scoped; deliberately minimal — section §六)
# ============================================================

SEMANTIC_COLUMN_ALIASES: Final[tuple[ColumnSemanticAlias, ...]] = (
    # ---- document (t2s_eval.documents) ----
    _concept(
        "document_id", ENTITY_DOCUMENT, "documents", "id",
        ("id", "document_id"),
    ),
    _concept(
        "document_title", ENTITY_DOCUMENT, "documents", "title",
        ("title", "document_title"),
    ),
    _concept(
        "document_file_type", ENTITY_DOCUMENT, "documents", "file_type",
        ("file_type", "document_file_type"),
    ),
    _concept(
        "document_created_at", ENTITY_DOCUMENT, "documents", "created_at",
        ("created_at", "document_created_at"),
    ),
    _concept(
        "document_count", ENTITY_DOCUMENT, "documents", "*",
        (
            "count", "total_count", "total_documents",
            "document_count", "documents_count", "num_documents",
        ),
        aggregate=AGGREGATE_COUNT,
    ),
    # ---- chunk (t2s_eval.chunks) ----
    _concept(
        "chunk_id", ENTITY_CHUNK, "chunks", "id",
        ("id", "chunk_id"),
    ),
    _concept(
        "chunk_content", ENTITY_CHUNK, "chunks", "content",
        ("content", "chunk_content"),
    ),
    _concept(
        "chunk_token_count", ENTITY_CHUNK, "chunks", "token_count",
        ("token_count", "chunk_token_count"),
    ),
    _concept(
        "chunk_count", ENTITY_CHUNK, "chunks", "*",
        (
            "count", "total_count", "total_chunks",
            "chunk_count", "chunks_count", "num_chunks",
        ),
        aggregate=AGGREGATE_COUNT,
    ),
    # ---- inventory item (t2s_eval.project_a_inventory / _b_inventory) ----
    _concept(
        "inventory_item_code", ENTITY_INVENTORY_ITEM, "inventory",
        "item_code", ("item_code", "inventory_item_code"),
    ),
    _concept(
        "inventory_qty", ENTITY_INVENTORY_ITEM, "inventory", "qty",
        ("qty", "quantity", "inventory_qty"),
    ),
)

SEMANTIC_COLUMN_ALIAS_BY_NAME: Final[Mapping[str, ColumnSemanticAlias]] = {
    concept.semantic_name: concept for concept in SEMANTIC_COLUMN_ALIASES
}


def _build_alias_index() -> Mapping[str, tuple[ColumnSemanticAlias, ...]]:
    index: dict[str, list[ColumnSemanticAlias]] = {}
    for concept in SEMANTIC_COLUMN_ALIASES:
        for alias in sorted(concept.aliases):
            index.setdefault(alias, []).append(concept)
    return {key: tuple(value) for key, value in index.items()}


_ALIAS_INDEX: Final[Mapping[str, tuple[ColumnSemanticAlias, ...]]] = (
    _build_alias_index()
)


# ============================================================
# Per-case alias context (derived from the 3.9.14 saved SQL)
# ============================================================

def _ctx(entity: str, aggregate: str | None = None) -> ColumnAliasContext:
    return ColumnAliasContext(entity=entity, aggregate=aggregate)


#: Entity context per (case_id, required_column), derived from the saved
#: Phase 3.9.14 SQL projection — NOT from the actual column names, so it
#: cannot silently adapt the ground truth to whatever the LLM produced.
#: Evaluation-only metadata; it never changes ``required_columns`` or
#: ``expected_rows`` (section §十二).
CASE_ALIAS_CONTEXTS: Final[
    Mapping[str, Mapping[str, ColumnAliasContext]]
] = {
    "simple_document_list": {
        "id": _ctx(ENTITY_DOCUMENT),
    },
    "top_n_chunks_by_token_count": {
        "id": _ctx(ENTITY_CHUNK),
        "token_count": _ctx(ENTITY_CHUNK),
    },
    "chunks_ordered_by_token_count": {
        "id": _ctx(ENTITY_CHUNK),
        "token_count": _ctx(ENTITY_CHUNK),
    },
    "aggregate_document_count": {
        "count": _ctx(ENTITY_DOCUMENT, AGGREGATE_COUNT),
    },
    "group_by_chunk_count_per_document": {
        "id": _ctx(ENTITY_DOCUMENT),
        "chunk_count": _ctx(ENTITY_CHUNK, AGGREGATE_COUNT),
    },
    "having_chunk_count_greater_than": {
        "id": _ctx(ENTITY_DOCUMENT),
    },
    "join_chunk_with_parent_document": {
        "id": _ctx(ENTITY_CHUNK),
        "title": _ctx(ENTITY_DOCUMENT),
    },
    "date_filter_created_after": {
        "id": _ctx(ENTITY_DOCUMENT),
    },
    "limit_first_10_documents": {
        "id": _ctx(ENTITY_DOCUMENT),
    },
    "semantic_dependent_document_and_chunk": {
        "id": _ctx(ENTITY_DOCUMENT),
        "chunk_count": _ctx(ENTITY_CHUNK, AGGREGATE_COUNT),
    },
    "project_a_inventory": {
        "item_code": _ctx(ENTITY_INVENTORY_ITEM),
        "qty": _ctx(ENTITY_INVENTORY_ITEM),
    },
    "project_b_inventory": {
        "item_code": _ctx(ENTITY_INVENTORY_ITEM),
        "qty": _ctx(ENTITY_INVENTORY_ITEM),
    },
}

#: N/A cases never receive an alias context (sections §十二 / 3.9.17 Rule 6).
NA_CASE_ALIAS_CONTEXTS: Final[frozenset[str]] = frozenset({
    "filtered_documents_by_file_type",
    "safety_delete_all_documents",
})


def alias_context_for_case(
    case_id: str | None,
) -> Mapping[str, ColumnAliasContext]:
    """Return the per-column alias context declared for ``case_id``.

    Unknown / N-A case ids yield an empty mapping, which means: only
    exact matches are accepted (never a guessed alias).
    """
    if not case_id:
        return {}
    if case_id in NA_CASE_ALIAS_CONTEXTS:
        return {}
    return CASE_ALIAS_CONTEXTS.get(str(case_id), {})


# ============================================================
# Resolution
# ============================================================

def _first_actual_in_alias_set(
    actual_columns: Sequence[str],
    lowered_actual: Sequence[str],
    aliases: frozenset[str],
) -> str | None:
    for original, lowered in zip(actual_columns, lowered_actual):
        if lowered in aliases:
            return original
    return None


def _unknown(
    expected: str,
    *,
    semantic_name: str | None = None,
    entity: str | None = None,
    detail: str,
) -> ColumnAliasResolution:
    return ColumnAliasResolution(
        expected_column=expected,
        actual_column=None,
        semantic_name=semantic_name,
        entity=entity,
        match_kind=MATCH_NONE,
        diagnostic=DIAGNOSTIC_UNKNOWN_COLUMN,
        detail=detail,
    )


def resolve_semantic_column(
    *,
    expected_column: str,
    actual_columns: Sequence[str],
    context: ColumnAliasContext | None = None,
) -> ColumnAliasResolution:
    """Resolve one expected semantic column against the actual projection.

    Pure, deterministic, offline. See the module docstring for the
    4-level priority and the aggregate rule.
    """
    expected = str(expected_column)
    expected_lower = expected.strip().lower()
    actual = tuple(str(column) for column in actual_columns)
    lowered_actual = tuple(str(column).strip().lower() for column in actual)

    # ---- Level 1: exact column name (highest priority) ----
    if expected_lower in lowered_actual:
        return ColumnAliasResolution(
            expected_column=expected,
            actual_column=actual[lowered_actual.index(expected_lower)],
            semantic_name=expected,
            entity=None if context is None else context.entity,
            match_kind=MATCH_EXACT,
            diagnostic=DIAGNOSTIC_EXACT_MATCH,
            detail="exact column name match",
        )

    # ---- Level 2: explicit semantic alias ----------------------------
    # The expected column IS the canonical semantic_name of a concept, so
    # the concept is unambiguous without any context.
    canonical = SEMANTIC_COLUMN_ALIAS_BY_NAME.get(expected_lower)
    if canonical is not None:
        hit = _first_actual_in_alias_set(actual, lowered_actual, canonical.aliases)
        if hit is not None:
            return ColumnAliasResolution(
                expected_column=expected,
                actual_column=hit,
                semantic_name=canonical.semantic_name,
                entity=canonical.entity,
                match_kind=MATCH_ALIAS,
                diagnostic=DIAGNOSTIC_ALIAS_MATCH,
                detail=(
                    f"explicit semantic alias: {expected!r} -> {hit!r} "
                    f"(semantic_name={canonical.semantic_name!r}, "
                    f"entity={canonical.entity!r})"
                ),
            )
        return _unknown(
            expected,
            semantic_name=canonical.semantic_name,
            entity=canonical.entity,
            detail=(
                f"canonical semantic column {expected!r} declared, but no "
                f"actual column matches its alias set "
                f"{sorted(canonical.aliases)}"
            ),
        )

    # ---- Level 3: context-aware alias -------------------------------
    candidates = _ALIAS_INDEX.get(expected_lower, ())
    if not candidates:
        return _unknown(
            expected,
            detail=f"no alias concept declares {expected!r}; unknown alias",
        )
    if context is None or not context.entity:
        return _unknown(
            expected,
            detail=(
                f"{expected!r} is a bare alias shared by "
                f"{len(candidates)} concept(s); an explicit entity context "
                f"is required (deterministic evaluation, no guessing)"
            ),
        )
    scoped = [c for c in candidates if c.entity == context.entity]
    if context.aggregate:
        preferred = [c for c in scoped if c.aggregate == context.aggregate]
        if preferred:
            scoped = preferred
    if len(scoped) != 1:
        return _unknown(
            expected,
            entity=context.entity,
            detail=(
                f"ambiguous alias: {len(scoped)} concept(s) match "
                f"{expected!r} under entity={context.entity!r}; "
                f"resolved as UNKNOWN"
            ),
        )
    concept = scoped[0]
    hit = _first_actual_in_alias_set(actual, lowered_actual, concept.aliases)
    if hit is None:
        return _unknown(
            expected,
            semantic_name=concept.semantic_name,
            entity=concept.entity,
            detail=(
                f"concept {concept.semantic_name!r} "
                f"(entity={concept.entity!r}) selected by context, but no "
                f"actual column matches its alias set "
                f"{sorted(concept.aliases)}"
            ),
        )
    return ColumnAliasResolution(
        expected_column=expected,
        actual_column=hit,
        semantic_name=concept.semantic_name,
        entity=concept.entity,
        match_kind=MATCH_ALIAS,
        diagnostic=DIAGNOSTIC_ALIAS_MATCH,
        detail=(
            f"context-aware alias: {expected!r} -> {hit!r} "
            f"(semantic_name={concept.semantic_name!r}, "
            f"entity={concept.entity!r}, "
            f"aggregate={concept.aggregate_kind or 'none'})"
        ),
    )


def resolve_required_columns(
    required_columns: Sequence[str],
    actual_columns: Sequence[str],
    contexts: Mapping[str, ColumnAliasContext] | None = None,
) -> tuple[ColumnAliasResolution, ...]:
    """Resolve every required column of one semantic expectation."""
    lookup: Mapping[str, ColumnAliasContext] = contexts or {}
    normalized = {
        str(key).strip().lower(): value for key, value in lookup.items()
    }
    return tuple(
        resolve_semantic_column(
            expected_column=column,
            actual_columns=actual_columns,
            context=normalized.get(str(column).strip().lower()),
        )
        for column in required_columns
    )


def validate_alias_registry() -> tuple[str, ...]:
    """Structural self-check of the registry (used by tests).

    Empty tuple == registry is internally consistent.
    """
    problems: list[str] = []

    names = [concept.semantic_name for concept in SEMANTIC_COLUMN_ALIASES]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        problems.append(f"duplicate semantic_name: {duplicates}")

    for concept in SEMANTIC_COLUMN_ALIASES:
        if not concept.source_table or not concept.source_column:
            problems.append(
                f"{concept.semantic_name}: source_table/source_column "
                f"must be non-empty"
            )

    # Two concepts of the SAME entity must never share an alias: such a
    # collision can never be resolved by entity context.
    by_entity: dict[tuple[str, str], dict[str, str]] = {}
    for concept in SEMANTIC_COLUMN_ALIASES:
        key = (concept.entity, concept.aggregate_kind)
        bucket = by_entity.setdefault(key, {})
        for alias in sorted(concept.aliases):
            if alias in bucket:
                problems.append(
                    f"alias {alias!r} claimed by both "
                    f"{bucket[alias]!r} and {concept.semantic_name!r} "
                    f"for entity={concept.entity!r} "
                    f"aggregate={concept.aggregate_kind!r}"
                )
            else:
                bucket[alias] = concept.semantic_name

    # Per-case context must not reference N/A cases.
    for case_id in CASE_ALIAS_CONTEXTS:
        if case_id in NA_CASE_ALIAS_CONTEXTS:
            problems.append(
                f"N/A case {case_id!r} must not declare an alias context"
            )
    return tuple(problems)
