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

Cross-entity guard (Phase 3.9.19, sections §五 / §十): an actual column
name claimed by concepts of more than one entity (``id``, ``count``) is
intrinsically ambiguous. It only matches when the entity context is
explicit AND equals the chosen concept's entity. Bound to the wrong
entity, or with no context at all, it resolves to ``UNKNOWN_COLUMN``
instead of being guessed — so ``documents.id`` never bleeds into
``chunk_id``. The same resolver, with the same roles (required /
optional / forbidden), is reused everywhere, so a column can never be
both an alias and an undeclared extra.

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
    "ROLE_REQUIRED",
    "ROLE_OPTIONAL",
    "ROLE_FORBIDDEN",
    "ROLE_VALUES",
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
    "resolve_column_group",
    "resolve_declared_columns",
    "is_cross_entity_alias",
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

#: Phase 3.9.19 — which part of ``semantic_expectation`` a resolution
#: belongs to. The SAME resolver is used for all three roles, so
#: ``required`` / ``optional`` / ``forbidden`` can never disagree about
#: whether two column names denote the same business concept.
ROLE_REQUIRED: Final[str] = "required"
ROLE_OPTIONAL: Final[str] = "optional"
ROLE_FORBIDDEN: Final[str] = "forbidden"
ROLE_VALUES: Final[frozenset[str]] = frozenset({
    ROLE_REQUIRED, ROLE_OPTIONAL, ROLE_FORBIDDEN,
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
    """Outcome of resolving one expected semantic column.

    Phase 3.9.19: ``role`` records whether the column came from
    ``required_columns`` / ``optional_columns`` / ``forbidden_columns``.
    All three roles share the same resolver, so the classification is
    consistent across the whole expectation.
    """
    expected_column: str
    actual_column: str | None = None
    semantic_name: str | None = None
    entity: str | None = None
    match_kind: str = MATCH_NONE
    diagnostic: str = DIAGNOSTIC_UNKNOWN_COLUMN
    detail: str = ""
    role: str = ROLE_REQUIRED

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
            "role": self.role,
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
    role: str = ROLE_REQUIRED,
) -> ColumnAliasResolution:
    return ColumnAliasResolution(
        expected_column=expected,
        actual_column=None,
        semantic_name=semantic_name,
        entity=entity,
        match_kind=MATCH_NONE,
        diagnostic=DIAGNOSTIC_UNKNOWN_COLUMN,
        detail=detail,
        role=role,
    )


def is_cross_entity_alias(column_name: str) -> bool:
    """True if the projection name is claimed by concepts of >1 entity.

    Such a name (e.g. ``id``, ``count``) is intrinsically ambiguous: it can
    only be bound to a concept when the entity context is explicit and
    matches that concept (Phase 3.9.19, sections §五 / §十). Without the
    context it resolves to ``UNKNOWN_COLUMN`` — never a guess.
    """
    concepts = _ALIAS_INDEX.get(str(column_name).strip().lower(), ())
    return len({concept.entity for concept in concepts}) > 1


def resolve_semantic_column(
    *,
    expected_column: str,
    actual_columns: Sequence[str],
    context: ColumnAliasContext | None = None,
    role: str = ROLE_REQUIRED,
) -> ColumnAliasResolution:
    """Resolve one expected semantic column against the actual projection.

    Pure, deterministic, offline. See the module docstring for the
    4-level priority and the aggregate rule.

    Phase 3.9.19: ``role`` tags the resolution with the part of the
    expectation it came from (required / optional / forbidden). The
    cross-entity guard (below) additionally requires an entity context
    before binding an ambiguous actual column name (``id`` / ``count``)
    to any concept.
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
            role=role,
            detail="exact column name match",
        )

    # ---- Level 2: explicit semantic alias ----------------------------
    # The expected column IS the canonical semantic_name of a concept, so
    # the concept is unambiguous without any context — but the actual
    # column it binds to may itself be a cross-entity alias (e.g. ``id``),
    # in which case the entity context is authoritative (Case D / Case E).
    canonical = SEMANTIC_COLUMN_ALIAS_BY_NAME.get(expected_lower)
    if canonical is not None:
        hit = _first_actual_in_alias_set(actual, lowered_actual, canonical.aliases)
        if hit is not None:
            if is_cross_entity_alias(hit) and not (
                context is not None and context.entity == canonical.entity
            ):
                return _unknown(
                    expected,
                    semantic_name=canonical.semantic_name,
                    entity=canonical.entity,
                    role=role,
                    detail=(
                        f"actual column {hit!r} is a cross-entity alias; an "
                        f"explicit entity context matching "
                        f"entity={canonical.entity!r} is required to bind it "
                        f"to {canonical.semantic_name!r} (deterministic, no "
                        f"guessing)"
                    ),
                )
            return ColumnAliasResolution(
                expected_column=expected,
                actual_column=hit,
                semantic_name=canonical.semantic_name,
                entity=canonical.entity,
                match_kind=MATCH_ALIAS,
                diagnostic=DIAGNOSTIC_ALIAS_MATCH,
                role=role,
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
            role=role,
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
            role=role,
            detail=f"no alias concept declares {expected!r}; unknown alias",
        )
    if context is None or not context.entity:
        return _unknown(
            expected,
            role=role,
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
            role=role,
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
            role=role,
            detail=(
                f"concept {concept.semantic_name!r} "
                f"(entity={concept.entity!r}) selected by context, but no "
                f"actual column matches its alias set "
                f"{sorted(concept.aliases)}"
            ),
        )
    # Cross-entity guard: a bare actual column (``count``) must agree with
    # the context entity. At this level ``scoped`` was already filtered by
    # ``context.entity``, so this only rejects a residual mismatch.
    if is_cross_entity_alias(hit) and concept.entity != context.entity:
        return _unknown(
            expected,
            semantic_name=concept.semantic_name,
            entity=concept.entity,
            role=role,
            detail=(
                f"actual column {hit!r} is a cross-entity alias; entity "
                f"context required to bind it to "
                f"{concept.semantic_name!r} (entity={concept.entity!r})"
            ),
        )
    return ColumnAliasResolution(
        expected_column=expected,
        actual_column=hit,
        semantic_name=concept.semantic_name,
        entity=concept.entity,
        match_kind=MATCH_ALIAS,
        diagnostic=DIAGNOSTIC_ALIAS_MATCH,
        role=role,
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
    """Resolve every required column of one semantic expectation.

    Thin wrapper over :func:`resolve_column_group` with ``role=required``.
    """
    return resolve_column_group(
        required_columns, actual_columns, contexts, ROLE_REQUIRED
    )


def resolve_column_group(
    columns: Sequence[str],
    actual_columns: Sequence[str],
    contexts: Mapping[str, ColumnAliasContext] | None,
    role: str,
) -> tuple[ColumnAliasResolution, ...]:
    """Resolve one group (required/optional/forbidden) of expected columns.

    ``role`` tags every returned resolution so the semantic checker can
    attribute it to the right part of the expectation (Phase 3.9.19).
    """
    if role not in ROLE_VALUES:
        raise ValueError(f"unknown alias role: {role!r}")
    lookup: Mapping[str, ColumnAliasContext] = contexts or {}
    normalized = {
        str(key).strip().lower(): value for key, value in lookup.items()
    }
    return tuple(
        resolve_semantic_column(
            expected_column=column,
            actual_columns=actual_columns,
            context=normalized.get(str(column).strip().lower()),
            role=role,
        )
        for column in columns
    )


def resolve_declared_columns(
    required_columns: Sequence[str],
    optional_columns: Sequence[str],
    forbidden_columns: Sequence[str],
    actual_columns: Sequence[str],
    contexts: Mapping[str, ColumnAliasContext] | None = None,
) -> tuple[ColumnAliasResolution, ...]:
    """Resolve required + optional + forbidden columns in one pass.

    Phase 3.9.19 (sections §四 / §六 / §七): the SAME resolver, with the
    SAME entity context, is applied to all three groups, so a column can
    never be simultaneously an alias for a required/optional concept AND
    an undeclared extra column. The caller partitions the result by
    ``ColumnAliasResolution.role``. Unmatched entries are kept so the
    caller can report ``MISSING_REQUIRED`` / ``UNDECLARED_EXTRA`` etc.
    """
    return (
        resolve_column_group(
            required_columns, actual_columns, contexts, ROLE_REQUIRED
        )
        + resolve_column_group(
            optional_columns, actual_columns, contexts, ROLE_OPTIONAL
        )
        + resolve_column_group(
            forbidden_columns, actual_columns, contexts, ROLE_FORBIDDEN
        )
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
