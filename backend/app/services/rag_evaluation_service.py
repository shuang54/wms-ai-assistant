"""RAG Retrieval Evaluation Service（Phase 3.5.7）。

目的：对量化结果（Collection）进行可重复运行的检索质量评估，
建立 Baseline（原始 Top-K Keyword Hit Rate）作为后续优化参考。

数据：
    Query
      ↓
  Evaluation Service（仅评估，不调 LLM）
      ↓
  VectorSearchService.search（复用，不重新实现 embedding / 距离计算）
      ↓
  Top-K Results
      ↓
  Keyword / document_id 匹配
      ↓
  RetrievalEvaluationSummary

设计原则：
    - 复用 VectorSearchService（不重新实现 embedding / pgvector SQL）
    - 不调用 LLM（评估阶段仅测检索，不测生成）
    - 单 case 失败 → 结构化失败记录（不阻断其余 case）
    - DTO：frozen dataclass；不含 ORM / embedding 向量 / DB 连接信息

评价：
    第一版只实现 Top-K Keyword Hit Rate（每个 case 全部关键词命中即成功）。
    后续如需更复杂指标（MRR / NDCG / Recall@K），再独立 Phase。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

from backend.app.services.vector_search_service import (
    VectorSearchResult,
    VectorSearchService,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# ============================================================
# DTO（frozen dataclass）
# ============================================================

@dataclass(frozen=True)
class RetrievalEvaluationCase:
    """单个评估 case。

    所有匹配信息都不假设具体 ID（避免依赖 DB 当前状态）：
        - expected_keywords:    任意 Top-K 结果的 content / metadata 中
                                出现全部关键字 → matched=True
        - expected_document_id:  任意 Top-K 结果属于该文档 → 视为匹配
                                （用于特定 case，可选；优先用 keywords）
        - expected_source:      metadata.source 中包含该字符串
        - expected_title:       metadata.title 中包含该字符串

    不暴露 embedding vector / DB connection / API Key。
    """

    case_id: str
    query: str
    expected_keywords: tuple[str, ...] = field(default_factory=tuple)
    expected_document_id: int | None = None
    expected_source: str | None = None
    expected_title: str | None = None


@dataclass(frozen=True)
class RetrievalEvaluationResult:
    """单个 case 的评估结果。

    error 为 None 表示正常评估；非 None 表示该 case 在 search 阶段失败。
    failed_case 状态由 error / matched 综合得出（在 Summary 中计算）。
    """

    case_id: str
    query: str
    top_k: int
    matched: bool
    results_count: int
    matched_result_indexes: tuple[int, ...] = field(default_factory=tuple)
    matched_keywords: tuple[str, ...] = field(default_factory=tuple)
    missing_keywords: tuple[str, ...] = field(default_factory=tuple)
    document_id_match: bool = True
    error: str | None = None


@dataclass(frozen=True)
class RetrievalEvaluationSummary:
    """评估汇总。

    hit_rate = matched_cases / total_cases（total_cases == 0 时为 0.0）。
    """

    total_cases: int
    matched_cases: int
    failed_cases: int
    hit_rate: float
    top_k: int
    results: tuple[RetrievalEvaluationResult, ...] = field(default_factory=tuple)


# ============================================================
# Top-K 离线评估 DTO（Phase 3.5.11）
# ============================================================

@dataclass(frozen=True)
class TopKEvaluationEntry:
    """单个 Top-K 档位的离线评估统计。

    similarity 统计口径：该 Top-K 档位下、全部 case 的 Top-K 结果
    （即每个 case 召回前 K 条）的 similarity 聚合；
    所有 case 都无结果时 min / max 为 None，average 为 0.0。
    """

    top_k: int
    case_count: int
    hit_count: int
    hit_rate: float
    error_count: int
    average_similarity: float
    min_similarity: float | None
    max_similarity: float | None
    results: tuple[RetrievalEvaluationResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TopKEvaluationSummary:
    """多档 Top-K 离线评估汇总（按传入 top_ks 顺序）。"""

    top_ks: tuple[int, ...]
    entries: tuple[TopKEvaluationEntry, ...] = field(default_factory=tuple)


# ============================================================
# 匹配逻辑（轻量、可解释；不做 NLP / LLM 判断）
# ============================================================

def _norm(s: str) -> str:
    return s.strip().lower()


def _metadata_value(metadata: Mapping[str, object] | None, *keys: str) -> str:
    """从 metadata 中按优先级（*keys）取值，统一转为字符串后拼接。"""
    if not metadata:
        return ""
    parts: list[str] = []
    for k in keys:
        v = metadata.get(k)
        if v is None:
            continue
        parts.append(str(v))
    return "\n".join(parts)


def _match_case(
    *,
    case: RetrievalEvaluationCase,
    results: list[VectorSearchResult],
) -> tuple[bool, tuple[int, ...], tuple[str, ...], tuple[str, ...], bool]:
    """返回 (matched, matched_indexes, matched_keywords, missing_keywords, doc_matched)。

    matched:            所有 expected_keywords AND expected_document_id（若指定）都通过
    matched_indexes:    命中的 result 下标（只要该 result 命中任何一项即计入）
    matched_keywords:   命中的关键字集合
    missing_keywords:   未命中的关键字集合
    doc_matched:        expected_document_id 是否被任一 result 命中
    """
    matched_indexes: list[int] = []
    matched_kw: set[str] = set()
    missing_kw: set[str] = set(case.expected_keywords)
    doc_matched = case.expected_document_id is None  # 未指定视为通过

    for kw in case.expected_keywords:
        nkw = _norm(kw)
        # 一个 keyword 只要任意 result 命中即可
        hit = False
        for r in results:
            if nkw in _norm(r.content) or nkw in _norm(
                _metadata_value(r.metadata, "source", "title", "heading_path_text")
            ):
                hit = True
                break
        if hit:
            matched_kw.add(kw)
            missing_kw.discard(kw)

    for idx, r in enumerate(results):
        if not matched_kw and not missing_kw and not case.expected_keywords:
            # 无 keywords 要求 → 只要有结果就记入索引
            matched_indexes.append(idx)
        else:
            if any(_norm(kw) in _norm(r.content) for kw in matched_kw):
                matched_indexes.append(idx)

    if case.expected_document_id is not None:
        doc_matched = any(
            r.document_id == case.expected_document_id for r in results
        )

    # matched：keywords 全部命中 AND document_id（若指定）命中
    kw_ok = not missing_kw
    matched = kw_ok and doc_matched
    return matched, tuple(matched_indexes), tuple(matched_kw), tuple(missing_kw), doc_matched


# ============================================================
# Service
# ============================================================

class RagEvaluationService:
    """RAG 检索质量评估服务（只读）。

    依赖：
        vector_search_service: VectorSearchService 实例（复用检索能力）

    不持有 LLMClient —— 本服务明确禁止调用 LLM，避免产生 DeepSeek 费用。
    """

    def __init__(self, *, vector_search_service: VectorSearchService) -> None:
        self._vector_search_service = vector_search_service

    async def evaluate(
        self,
        cases: list[RetrievalEvaluationCase],
        *,
        top_k: int = 5,
    ) -> RetrievalEvaluationSummary:
        """对所有 cases 顺序执行评估，生成汇总。

        单 case 失败策略：
            search 抛任何异常 → 该 case 的 RetrievalEvaluationResult.error 被填入，
            matched=False，计入 failed_cases；**不**阻断后续 case 的评估。

        空 cases：直接返回 hit_rate=0 的空 Summary（避免除零）。
        """
        if not cases:
            return RetrievalEvaluationSummary(
                total_cases=0,
                matched_cases=0,
                failed_cases=0,
                hit_rate=0.0,
                top_k=top_k,
                results=(),
            )

        results: list[RetrievalEvaluationResult] = []
        matched_cases = 0
        failed_cases = 0

        for case in cases:
            try:
                rows = await self._vector_search_service.search(
                    case.query, top_k=top_k
                )
            except Exception as exc:  # noqa: BLE001 — 评估阶段记录错误而非吞掉
                logger.warning(
                    "evaluation case failed: case_id=%s error=%s",
                    case.case_id,
                    exc,
                    extra={"case_id": case.case_id, "error_type": type(exc).__name__},
                )
                results.append(
                    RetrievalEvaluationResult(
                        case_id=case.case_id,
                        query=case.query,
                        top_k=top_k,
                        matched=False,
                        results_count=0,
                        error=str(exc),
                    )
                )
                failed_cases += 1
                continue

            matched, idx, mkw, mkw_miss, _doc = _match_case(case=case, results=rows)
            if matched:
                matched_cases += 1
            results.append(
                RetrievalEvaluationResult(
                    case_id=case.case_id,
                    query=case.query,
                    top_k=top_k,
                    matched=matched,
                    results_count=len(rows),
                    matched_result_indexes=idx,
                    matched_keywords=mkw,
                    missing_keywords=mkw_miss,
                    document_id_match=(
                        case.expected_document_id is None or _doc
                    ),
                )
            )

        hit_rate = matched_cases / len(cases)
        return RetrievalEvaluationSummary(
            total_cases=len(cases),
            matched_cases=matched_cases,
            failed_cases=failed_cases,
            hit_rate=hit_rate,
            top_k=top_k,
            results=tuple(results),
        )

    # ============================================================
    # Top-K 离线评估（Phase 3.5.11 新增；不改变上面 evaluate 的行为）
    # ============================================================

    DEFAULT_TOP_KS: tuple[int, ...] = (1, 3, 5, 10)

    async def evaluate_top_k(
        self,
        cases: list[RetrievalEvaluationCase],
        *,
        top_ks: tuple[int, ...] | list[int] = DEFAULT_TOP_KS,
    ) -> TopKEvaluationSummary:
        """对同一批 cases 在多个 Top-K 档位下做离线检索评估。

        实现（复用 + 前缀截断，避免重复 Embedding 调用）：

            1. 每个 case 只调用一次 search(query, top_k=max(top_ks))
            2. 由于 search 结果按 similarity 降序（distance 升序）排列，
               Top-K'（K' < max）的结果恒为该列表的前 K' 条前缀
               —— 直接截断，不重复发起 Embedding / SQL。
            3. 对每个档位分别跑既有 `_match_case`（匹配逻辑与 evaluate 完全一致）
            4. 汇总每档位的 hit_count / hit_rate / similarity 统计

        不调用 LLM（本服务不持有 LLMClient）。

        Args:
            cases:  评估 case 列表。
            top_ks: 档位（会去重保序）。所有档位都必须落在
                    VectorSearchService 的合法 top_k 范围内，
                    否则底层抛 VectorSearchParameterError（原样透传）。

        Returns:
            TopKEvaluationSummary（cases 为空时 entries 的每档统计均为零值）。
        """
        ks = tuple(dict.fromkeys(int(k) for k in top_ks))
        if not cases:
            return TopKEvaluationSummary(
                top_ks=ks,
                entries=tuple(
                    TopKEvaluationEntry(
                        top_k=k,
                        case_count=0,
                        hit_count=0,
                        hit_rate=0.0,
                        error_count=0,
                        average_similarity=0.0,
                        min_similarity=None,
                        max_similarity=None,
                    )
                    for k in ks
                ),
            )
        if not ks:
            return TopKEvaluationSummary(top_ks=(), entries=())

        max_k = max(ks)
        per_k_results: dict[int, list[RetrievalEvaluationResult]] = {k: [] for k in ks}
        hit_counts: dict[int, int] = {k: 0 for k in ks}
        error_counts: dict[int, int] = {k: 0 for k in ks}
        sims_by_k: dict[int, list[float]] = {k: [] for k in ks}

        for case in cases:
            try:
                rows = await self._vector_search_service.search(
                    case.query, top_k=max_k
                )
            except Exception as exc:  # noqa: BLE001 — 与 evaluate() 相同的容错策略
                logger.warning(
                    "top-k evaluation case failed: case_id=%s error=%s",
                    case.case_id,
                    exc,
                    extra={"case_id": case.case_id, "error_type": type(exc).__name__},
                )
                for k in ks:
                    per_k_results[k].append(
                        RetrievalEvaluationResult(
                            case_id=case.case_id,
                            query=case.query,
                            top_k=k,
                            matched=False,
                            results_count=0,
                            error=str(exc),
                        )
                    )
                    error_counts[k] += 1
                continue

            for k in ks:
                prefix = rows[:k]
                matched, idx, mkw, mkw_miss, doc = _match_case(
                    case=case, results=prefix
                )
                if matched:
                    hit_counts[k] += 1
                per_k_results[k].append(
                    RetrievalEvaluationResult(
                        case_id=case.case_id,
                        query=case.query,
                        top_k=k,
                        matched=matched,
                        results_count=len(prefix),
                        matched_result_indexes=idx,
                        matched_keywords=mkw,
                        missing_keywords=mkw_miss,
                        document_id_match=(
                            case.expected_document_id is None or doc
                        ),
                    )
                )
                sims_by_k[k].extend(r.similarity for r in prefix)

        entries = []
        for k in ks:
            sims = sims_by_k[k]
            entry = TopKEvaluationEntry(
                top_k=k,
                case_count=len(cases),
                hit_count=hit_counts[k],
                hit_rate=hit_counts[k] / len(cases),
                error_count=error_counts[k],
                average_similarity=(sum(sims) / len(sims)) if sims else 0.0,
                min_similarity=min(sims) if sims else None,
                max_similarity=max(sims) if sims else None,
                results=tuple(per_k_results[k]),
            )
            entries.append(entry)

        return TopKEvaluationSummary(top_ks=ks, entries=tuple(entries))


# ============================================================
# 便捷：从 JSON 文件加载 Evaluation Cases
# ============================================================

def load_cases_from_json(path: str) -> list[RetrievalEvaluationCase]:
    """从 JSON 文件加载 cases（用于真实评估测试）。

    支持两种 Schema（自动识别）：

        1. 顶层 array：
            [
              {"id": "case_001", "query": "...", "expected_keywords": [...]},
              ...
            ]

        2. 顶层 object（推荐，便于扩展 schema / 文档字段）：
            {
              "_schema_version": "1.0",
              "_fields": {...},
              "cases": [
                {"id": "case_001", "query": "...", ...},
                ...
              ]
            }

    一个 case：

            {
              "id": "case_001",
              "query": "采购入库的操作步骤是什么？",
              "expected_keywords": ["采购入库"],   // 全部命中才算成功
              "expected_document_id": null,       // 可选
              "expected_source": null,            // 可选
              "expected_title": null              // 可选
            }

    不暴露 / 不要求数据库中真实存在对应 ID —— 优先按 keywords 评估。
    """
    import json as _json
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(f"evaluation cases file not found: {path}")

    with open(path, encoding="utf-8") as fp:
        raw = _json.load(fp)

    if isinstance(raw, dict):
        items = raw.get("cases")
        if items is None:
            raise ValueError(
                "evaluation cases file object must contain a 'cases' array"
            )
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError(
            f"evaluation cases file must be a JSON array or object with 'cases', "
            f"got {type(raw).__name__}"
        )

    if not isinstance(items, list):
        raise ValueError(
            f"'cases' must be a JSON array, got {type(items).__name__}"
        )

    cases: list[RetrievalEvaluationCase] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"case #{idx} is not an object: {item!r}")
        case_id = item.get("id") or item.get("case_id")
        query = item.get("query")
        if not case_id or not query:
            raise ValueError(
                f"case #{idx} missing required field: id/query → {item!r}"
            )
        kws = item.get("expected_keywords") or ()
        if isinstance(kws, list):
            kws = tuple(str(x) for x in kws)
        cases.append(
            RetrievalEvaluationCase(
                case_id=str(case_id),
                query=str(query),
                expected_keywords=tuple(kws),
                expected_document_id=item.get("expected_document_id"),
                expected_source=item.get("expected_source"),
                expected_title=item.get("expected_title"),
            )
        )
    return cases


__all__ = [
    "RetrievalEvaluationCase",
    "RetrievalEvaluationResult",
    "RetrievalEvaluationSummary",
    "TopKEvaluationEntry",
    "TopKEvaluationSummary",
    "RagEvaluationService",
    "load_cases_from_json",
]