-- =============================================================================
-- 02_views.sql
--
-- Execution stage of a step = length of the LONGEST dependency chain from a
-- root step (STEP_DEP_ID = 0) to that step. A step therefore starts only after
-- every prerequisite has finished, and all steps sharing a stage can run in
-- parallel.
--
-- Algorithm notes
--   * The recursive CTE uses UNION (not UNION ALL) over (unit, step, depth)
--     and does not carry a path. Each step is visited at most once per depth,
--     so work is bounded by O(steps x stages x edges) instead of growing with
--     the number of distinct paths (which is exponential for wide DAGs).
--   * In a valid DAG of N steps no chain is longer than N. The walk stops at
--     depth N + 1, so a cycle cannot loop forever, and any step that reaches
--     depth > N is proof of a cycle.
-- =============================================================================

DROP VIEW IF EXISTS v_execution_plan;
DROP VIEW IF EXISTS v_dependency_issues;
DROP VIEW IF EXISTS v_step_depth;

-- -----------------------------------------------------------------------------
-- v_step_depth: longest-chain depth of every step reachable from a root.
-- -----------------------------------------------------------------------------
CREATE VIEW v_step_depth AS
WITH RECURSIVE
unit_size (unit_nbr, n_steps) AS (
    SELECT unit_nbr, COUNT(*)
    FROM   prog_name
    GROUP  BY unit_nbr
),
walk (unit_nbr, step_seq_id, depth) AS (
    -- Anchor: root steps.
    SELECT r.unit_nbr, r.step_seq_id, 1
    FROM   dependency_rules r
    WHERE  r.step_dep_id = 0

    UNION

    -- Recursive: a step is one deeper than the prerequisite that was just reached.
    SELECT r.unit_nbr, r.step_seq_id, w.depth + 1
    FROM   walk w
    JOIN   dependency_rules r
           ON  r.unit_nbr    = w.unit_nbr
           AND r.step_dep_id = w.step_seq_id
    JOIN   unit_size u
           ON  u.unit_nbr    = w.unit_nbr
    WHERE  w.depth <= u.n_steps            -- cycle guard (see header)
)
SELECT w.unit_nbr,
       w.step_seq_id,
       MAX(w.depth) AS max_depth,
       u.n_steps
FROM   walk w
JOIN   unit_size u ON u.unit_nbr = w.unit_nbr
GROUP  BY w.unit_nbr, w.step_seq_id, u.n_steps;

-- -----------------------------------------------------------------------------
-- v_dependency_issues: anything that makes a unit's plan unsafe to run.
-- A unit with zero rows here is a valid DAG.
-- -----------------------------------------------------------------------------
CREATE VIEW v_dependency_issues AS
WITH
all_units (unit_nbr) AS (
    SELECT unit_nbr FROM prog_name
    UNION
    SELECT unit_nbr FROM dependency_rules
)
-- 1. Prerequisite refers to a step that does not exist in PROG_NAME.
SELECT r.unit_nbr,
       r.step_seq_id,
       'DANGLING_DEPENDENCY' AS issue_code,
       'rule ' || r.rule_id || ': step ' || r.step_seq_id
           || ' depends on unknown step ' || r.step_dep_id AS detail
FROM   dependency_rules r
WHERE  r.step_dep_id <> 0
AND    NOT EXISTS (SELECT 1 FROM prog_name p
                   WHERE p.unit_nbr = r.unit_nbr AND p.step_seq_id = r.step_dep_id)

UNION ALL
-- 2. Unit has no starting point.
SELECT a.unit_nbr, NULL, 'NO_ROOT_STEP',
       'no rule with STEP_DEP_ID = 0'
FROM   all_units a
WHERE  NOT EXISTS (SELECT 1 FROM dependency_rules r
                   WHERE r.unit_nbr = a.unit_nbr AND r.step_dep_id = 0)

UNION ALL
-- 3. Step is defined but has no dependency rule, so it would never be scheduled.
SELECT p.unit_nbr, p.step_seq_id, 'UNSCHEDULED_STEP',
       'step ' || p.step_seq_id || ' (' || p.step_prog_name || ') has no dependency rule'
FROM   prog_name p
WHERE  NOT EXISTS (SELECT 1 FROM dependency_rules r
                   WHERE r.unit_nbr = p.unit_nbr AND r.step_seq_id = p.step_seq_id)

UNION ALL
-- 4. Step has rules but cannot be reached from any root (e.g. an isolated cycle).
SELECT DISTINCT r.unit_nbr, r.step_seq_id, 'UNREACHABLE_STEP',
       'step ' || r.step_seq_id || ' is not reachable from a root step'
FROM   dependency_rules r
WHERE  NOT EXISTS (SELECT 1 FROM v_step_depth d
                   WHERE d.unit_nbr = r.unit_nbr AND d.step_seq_id = r.step_seq_id)

UNION ALL
-- 5. Step reachable from a root but part of / downstream of a cycle.
SELECT d.unit_nbr, d.step_seq_id, 'CYCLE_DETECTED',
       'step ' || d.step_seq_id || ' is on or behind a circular dependency'
FROM   v_step_depth d
WHERE  d.max_depth > d.n_steps;

-- -----------------------------------------------------------------------------
-- v_execution_plan: the run order for every VALID unit.
-- Units with any issue are excluded so a broken plan is never executed;
-- callers must check v_dependency_issues first (scripts/run.sh does).
-- Views carry no guaranteed order: ORDER BY unit_nbr, exec_stage, step_seq_id.
-- -----------------------------------------------------------------------------
CREATE VIEW v_execution_plan AS
SELECT d.unit_nbr,
       d.max_depth AS exec_stage,      -- same stage = may run in parallel
       d.step_seq_id,
       p.step_prog_name
FROM   v_step_depth d
JOIN   prog_name p
       ON  p.unit_nbr    = d.unit_nbr
       AND p.step_seq_id = d.step_seq_id
WHERE  d.unit_nbr NOT IN (SELECT unit_nbr FROM v_dependency_issues);
