-- Phase 3.9.13 — deterministic fixture data for t2s_eval.
--
-- Design intent (discriminative power):
--   * 4 documents, chunk counts deliberately different: 2 / 3 / 1 / 4
--     -> makes COUNT / GROUP BY / HAVING / JOIN distinguishable.
--   * token_count values are all distinct and tie-free
--     (10..100) -> ORDER BY ... DESC and Top-N are fully deterministic.
--   * file_type: 3 x 'md', 1 x 'txt' -> GROUP BY produces groups of
--     different sizes; HAVING COUNT(*) > 2 keeps exactly one group.
--   * created_at: 2 documents before 2026-01-01, 2 after
--     -> the date-filter case yields a stable 2-row answer.
--   * project_a / project_b hold the SAME item_code with DIFFERENT qty
--     (100 vs 999) -> project isolation is observable in the result.
--
-- All values are fixed constants. No NOW() / CURRENT_DATE / random data.

INSERT INTO t2s_eval.documents (id, title, file_type, created_at) VALUES
    (1, 'Alpha Report',  'md',  DATE '2025-06-10'),
    (2, 'Beta Notes',    'md',  DATE '2026-02-14'),
    (3, 'Gamma Guide',   'md',  DATE '2026-05-20'),
    (4, 'Delta Manual',  'txt', DATE '2025-11-05');

INSERT INTO t2s_eval.chunks (id, document_id, content, token_count) VALUES
    (1,  1, 'alpha intro',    10),
    (2,  1, 'alpha details',  20),
    (3,  2, 'beta intro',     30),
    (4,  2, 'beta details',   40),
    (5,  2, 'beta summary',   50),
    (6,  3, 'gamma intro',    60),
    (7,  4, 'delta intro',    70),
    (8,  4, 'delta details',  80),
    (9,  4, 'delta extra',    90),
    (10, 4, 'delta summary', 100);

INSERT INTO t2s_eval.project_a_inventory (item_code, qty) VALUES
    ('item_x', 100),
    ('item_y', 5);

INSERT INTO t2s_eval.project_b_inventory (item_code, qty) VALUES
    ('item_x', 999),
    ('item_y', 7);
