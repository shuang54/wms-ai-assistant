"""Phase 3.9.13 ground truth & fixture tests.

Offline tests never touch DeepSeek or PostgreSQL.
DB-touching tests are gated behind RUN_DB_TESTS (they only write inside
the dedicated `t2s_eval` schema).

Coverage (sections 29 / 30):
  * Fixture: schema creation, deterministic data, reset, isolation
  * Ground truth: referenced case IDs exist, no duplicates, expected
    columns exist, format valid, no security case
  * Determinism: setting up twice yields identical data
  * Project isolation: A != B
  * No production mutation / fixture integrity: statements only ever
    target `t2s_eval` (never `public`)
  * --check is offline
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.app.services.text_to_sql_evaluation_service import (
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_ground_truth_service import (
    EVAL_SCHEMA,
    FIXTURE_DATA_PATH,
    FIXTURE_SCHEMA_PATH,
    GROUND_TRUTH_PATH,
    GROUND_TRUTH_VERSION,
    assert_fixture_targets_eval_schema,
    load_ground_truth,
    read_fixture_sql,
    sha256_of,
    validate_ground_truth,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "text_to_sql"
    / "text_to_sql_regression.yaml"
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 to enable fixture database tests",
)


def _load_script_module():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module("scripts.setup_text_to_sql_ground_truth")


def _digest(path: Path) -> str | None:
    return (
        hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if path.exists() else None
    )


# ============================================================
# Ground truth (offline)
# ============================================================

class TestGroundTruthDocument:
    def test_version_and_schema(self) -> None:
        document = load_ground_truth()
        assert document.version == GROUND_TRUTH_VERSION
        assert document.schema == EVAL_SCHEMA

    def test_all_referenced_case_ids_exist(self) -> None:
        document = load_ground_truth()
        dataset = load_text_to_sql_regression_dataset()
        known = {case.case_id for case in dataset}
        for case_id in document.case_ids:
            assert case_id in known, case_id

    def test_no_duplicate_case_ids(self) -> None:
        document = load_ground_truth()
        assert len(document.case_ids) == len(set(document.case_ids))

    def test_no_security_case_included(self) -> None:
        """section 28: the safety case must have no result ground truth."""
        document = load_ground_truth()
        assert "safety_delete_all_documents" not in document.case_ids

    def test_expectation_format_valid(self) -> None:
        """Every expectation is a valid, supported checker type."""
        document = load_ground_truth()
        supported = {
            "exact_rows", "unordered_rows", "scalar", "column_values",
        }
        for case_id, expectation in document.expectations.items():
            assert expectation.type in supported, case_id
            if expectation.type == "scalar":
                assert expectation.value is not None, case_id
            if expectation.type == "column_values":
                assert expectation.column, case_id
                assert expectation.values, case_id
            if expectation.type in ("exact_rows", "unordered_rows"):
                assert isinstance(expectation.rows, tuple), case_id

    def test_validate_ground_truth_has_no_problems(self) -> None:
        document = load_ground_truth()
        dataset = load_text_to_sql_regression_dataset()
        assert validate_ground_truth(document, dataset) == []

    def test_duplicate_detection(self, tmp_path: Path) -> None:
        from backend.app.services.text_to_sql_ground_truth_service import (
            load_ground_truth as load,
        )

        payload = (
            'version: "1.0"\n'
            f"schema: {EVAL_SCHEMA}\n"
            "cases:\n"
            "  - case_id: aggregate_document_count\n"
            "    expectation:\n"
            "      type: scalar\n"
            "      value: 4\n"
            "  - case_id: aggregate_document_count\n"
            "    expectation:\n"
            "      type: scalar\n"
            "      value: 5\n"
        )
        path = tmp_path / "gt.yaml"
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate case_id"):
            load(path)

    def test_unknown_case_id_detected(self) -> None:
        document = load_ground_truth()
        # 伪造一个不存在的 case_id，应被 validate 捕获
        tampered = copy.copy(document)
        tampered.expectations.__setitem__("not_a_real_case", None)  # type: ignore
        problems = validate_ground_truth(
            tampered, load_text_to_sql_regression_dataset()
        )
        assert any("not_a_real_case" in item for item in problems)


# ============================================================
# Fixture integrity (offline, section 30)
# ============================================================

class TestFixtureIntegrity:
    def test_fixture_only_targets_eval_schema(self) -> None:
        schema_sql, data_sql = read_fixture_sql()
        assert assert_fixture_targets_eval_schema(schema_sql) == []
        assert assert_fixture_targets_eval_schema(data_sql) == []

    def test_fixture_never_mentions_public_production_schema(self) -> None:
        schema_sql, data_sql = read_fixture_sql()
        for name, sql_text in (
            ("schema_sql", schema_sql), ("data_sql", data_sql),
        ):
            assert "public." not in sql_text.lower(), name

    def test_fixture_detects_public_violation(self) -> None:
        bad = "CREATE TABLE public.leak (id int);"
        problems = assert_fixture_targets_eval_schema(bad)
        assert problems

    def test_fixture_files_exist(self) -> None:
        assert FIXTURE_SCHEMA_PATH.exists()
        assert FIXTURE_DATA_PATH.exists()
        assert GROUND_TRUTH_PATH.exists()


# ============================================================
# Offline --check (section 35)
# ============================================================

class TestGroundTruthCheckMode:
    def test_check_mode_does_not_write_files(self, monkeypatch):
        script = _load_script_module()
        targets = (
            script.SNAPSHOT_PATH, script.REPORT_PATH, DATASET_PATH,
            GROUND_TRUTH_PATH, FIXTURE_SCHEMA_PATH, FIXTURE_DATA_PATH,
        )
        before = {str(p): _digest(p) for p in targets}

        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0
        assert {str(p): _digest(p) for p in targets} == before

    def test_check_mode_does_not_invoke_llm_or_db(self, monkeypatch):
        script = _load_script_module()
        from backend.app.services.text_to_sql_service import TextToSQLService

        def forbid(*_a, **_kw):
            raise AssertionError("LLM/DB must not be used in --check mode")

        monkeypatch.setattr(TextToSQLService, "generate", forbid)
        monkeypatch.setattr(
            "backend.app.llm.client.get_default_llm_client", forbid
        )
        monkeypatch.setattr("backend.app.db.session.get_engine", forbid)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0

    def test_check_mode_fails_when_snapshot_missing(
        self, monkeypatch, tmp_path
    ):
        script = _load_script_module()
        monkeypatch.setattr(script, "SNAPSHOT_PATH", tmp_path / "nope.json")
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1


# ============================================================
# Snapshot (section 32)
# ============================================================

class TestGroundTruthSnapshot:
    def test_snapshot_schema(self):
        script = _load_script_module()
        payload = json.loads(
            script.SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        for key in (
            "phase", "dataset_version", "dataset_sha256",
            "ground_truth_version", "ground_truth_hash", "fixture_schema",
            "fixture_hashes", "total_cases", "result_evaluable_cases",
            "result_na_cases",
        ):
            assert key in payload, key
        assert payload["phase"] == "3.9.13"
        assert payload["fixture_schema"] == EVAL_SCHEMA
        assert payload["total_cases"] == 14

    def test_snapshot_hashes_match_current_files(self):
        script = _load_script_module()
        payload = json.loads(
            script.SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        assert payload["ground_truth_hash"] == sha256_of(GROUND_TRUTH_PATH)
        assert payload["fixture_hashes"]["schema_sql"] == sha256_of(
            FIXTURE_SCHEMA_PATH
        )
        assert payload["fixture_hashes"]["data_sql"] == sha256_of(
            FIXTURE_DATA_PATH
        )
        assert payload["dataset_sha256"] == _digest(DATASET_PATH)

    def test_snapshot_arithmetic(self):
        script = _load_script_module()
        payload = json.loads(
            script.SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        assert payload["result_evaluable_cases"] + (
            payload["result_na_cases"]
        ) == payload["total_cases"]
        assert payload["result_evaluable_cases"] == 12
        assert payload["result_na_cases"] == 2


# ============================================================
# Database-backed fixture tests (RUN_DB_TESTS=1)
# ============================================================

@requires_db
class TestFixtureDatabase:
    def _engine(self):
        from backend.app.db import reset_engine_cache
        from backend.app.db.session import get_engine

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None
        return engine

    def test_schema_created_and_data_deterministic(self):
        from sqlalchemy import text as sa_text

        script = _load_script_module()
        assert script.run_setup() == 0

        engine = self._engine()
        with engine.connect() as conn:
            for table, expected in script.EXPECTED_ROWS.items():
                value = conn.execute(
                    sa_text(f"SELECT count(*) FROM {EVAL_SCHEMA}.{table}")
                ).scalar()
                assert int(value or 0) == expected, table

    def test_real_foreign_key_exists(self):
        from sqlalchemy import inspect as sa_inspect

        engine = self._engine()
        foreign_keys = sa_inspect(engine).get_foreign_keys(
            "chunks", schema=EVAL_SCHEMA
        )
        assert any(
            fk.get("referred_schema") == EVAL_SCHEMA
            and fk.get("referred_table") == "documents"
            and "document_id" in (fk.get("constrained_columns") or ())
            for fk in foreign_keys
        )

    def test_reset_is_idempotent(self):
        from sqlalchemy import text as sa_text

        script = _load_script_module()
        assert script.run_setup() == 0

        def snapshot_rows():
            engine = self._engine()
            with engine.connect() as conn:
                return {
                    "documents": [
                        tuple(r) for r in conn.execute(sa_text(
                            f"SELECT * FROM {EVAL_SCHEMA}.documents "
                            "ORDER BY id"
                        )).fetchall()
                    ],
                    "chunks": [
                        tuple(r) for r in conn.execute(sa_text(
                            f"SELECT * FROM {EVAL_SCHEMA}.chunks ORDER BY id"
                        )).fetchall()
                    ],
                }

        first = snapshot_rows()
        assert script.run_setup() == 0
        assert snapshot_rows() == first

    def test_project_isolation_data_differs(self):
        from sqlalchemy import text as sa_text

        engine = self._engine()
        with engine.connect() as conn:
            qty_a = conn.execute(sa_text(
                f"SELECT qty FROM {EVAL_SCHEMA}.project_a_inventory "
                "WHERE item_code = 'item_x'"
            )).scalar()
            qty_b = conn.execute(sa_text(
                f"SELECT qty FROM {EVAL_SCHEMA}.project_b_inventory "
                "WHERE item_code = 'item_x'"
            )).scalar()
        assert qty_a != qty_b

    def test_public_business_tables_untouched(self):
        from sqlalchemy import text as sa_text

        engine = self._engine()
        with engine.connect() as conn:
            docs = conn.execute(
                sa_text("SELECT count(*) FROM knowledge_document")
            ).scalar()
            chunks = conn.execute(
                sa_text("SELECT count(*) FROM knowledge_chunk")
            ).scalar()
        assert int(docs or 0) == 0
        assert int(chunks or 0) == 0

    def test_all_expected_columns_exist(self):
        from sqlalchemy import text as sa_text

        engine = self._engine()
        with engine.connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema"
            ), {"schema": EVAL_SCHEMA}).fetchall()
        available = {str(column) for _table, column in rows}

        document = load_ground_truth()
        referenced = {
            expectation.column
            for expectation in document.expectations.values()
            if expectation.type == "column_values" and expectation.column
        }
        assert referenced, "ground truth should reference at least one column"
        for column in referenced:
            assert column in available, column

    def test_ground_truth_matches_actual_fixture_aggregates(self):
        from sqlalchemy import text as sa_text

        engine = self._engine()
        with engine.connect() as conn:
            count_docs = conn.execute(
                sa_text(f"SELECT count(*) FROM {EVAL_SCHEMA}.documents")
            ).scalar()
            per_doc = sorted(
                tuple(r) for r in conn.execute(sa_text(
                    f"SELECT document_id, count(*) FROM {EVAL_SCHEMA}.chunks "
                    "GROUP BY document_id"
                )).fetchall()
            )
            having_gt2 = sorted(
                tuple(r) for r in conn.execute(sa_text(
                    f"SELECT document_id, count(*) FROM {EVAL_SCHEMA}.chunks "
                    "GROUP BY document_id HAVING count(*) > 2"
                )).fetchall()
            )
            ordered = [
                r[0] for r in conn.execute(sa_text(
                    f"SELECT token_count FROM {EVAL_SCHEMA}.chunks "
                    "ORDER BY token_count DESC"
                )).fetchall()
            ]
            after_2026 = sorted(
                r[0] for r in conn.execute(sa_text(
                    f"SELECT id FROM {EVAL_SCHEMA}.documents "
                    "WHERE created_at > DATE '2026-01-01'"
                )).fetchall()
            )

        document = load_ground_truth()
        assert document.expectations[
            "aggregate_document_count"
        ].value == int(count_docs or 0)
        assert sorted(
            document.expectations[
                "group_by_chunk_count_per_document"
            ].rows
        ) == per_doc
        assert sorted(
            document.expectations["having_chunk_count_greater_than"].rows
        ) == having_gt2
        assert list(
            document.expectations["top_n_chunks_by_token_count"].values
        ) == ordered
        assert sorted(
            document.expectations["date_filter_created_after"].values
        ) == after_2026
