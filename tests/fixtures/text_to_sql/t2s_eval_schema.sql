-- Phase 3.9.13 — Text-to-SQL deterministic evaluation schema.
--
-- Purpose
--   Provide an isolated, minimal, deterministic dataset so that
--   Text-to-SQL regression cases can be verified at the RESULT level.
--
-- Rules
--   * Everything lives in the dedicated schema `t2s_eval`.
--     Nothing here touches `public` or any production schema.
--   * Real FK: chunks.document_id -> documents.id (no fake JOIN).
--   * Minimal columns: only what the current regression questions and
--     structural expectations require (title / file_type / created_at /
--     token_count / document_id). No production WMS columns are copied.
--   * Reset is allowed ONLY inside `t2s_eval`
--     (DROP SCHEMA ... CASCADE then re-create).
--
-- Idempotent: safe to run repeatedly.

DROP SCHEMA IF EXISTS t2s_eval CASCADE;

CREATE SCHEMA t2s_eval;

CREATE TABLE t2s_eval.documents (
    id         integer     PRIMARY KEY,
    title      text        NOT NULL,
    file_type  text        NOT NULL,
    created_at date        NOT NULL
);

CREATE TABLE t2s_eval.chunks (
    id           integer  PRIMARY KEY,
    document_id  integer  NOT NULL
                          REFERENCES t2s_eval.documents (id),
    content      text     NOT NULL,
    token_count  integer  NOT NULL
);

CREATE TABLE t2s_eval.project_a_inventory (
    item_code text    PRIMARY KEY,
    qty       integer NOT NULL
);

CREATE TABLE t2s_eval.project_b_inventory (
    item_code text    PRIMARY KEY,
    qty       integer NOT NULL
);

CREATE INDEX chunks_document_id_idx ON t2s_eval.chunks (document_id);
