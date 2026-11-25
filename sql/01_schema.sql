-- =============================================================================
-- 01_schema.sql  (SQLite >= 3.37, STRICT tables)
--
--   prog_name         : stored procedures (steps) that make up each batch job
--   dependency_rules  : edges of the step dependency graph (step -> prerequisite)
--
-- STEP_DEP_ID = 0 is the sentinel for "no prerequisite" (a root step).
-- NOTE: foreign keys are only enforced when PRAGMA foreign_keys = ON is set
--       on the connection.
-- =============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS prog_name (
    unit_nbr        INTEGER NOT NULL CHECK (unit_nbr > 0),
    step_seq_id     INTEGER NOT NULL CHECK (step_seq_id > 0),          -- 0 reserved for root sentinel
    step_prog_name  TEXT    NOT NULL CHECK (trim(step_prog_name) <> ''),

    CONSTRAINT pk_prog_name      PRIMARY KEY (unit_nbr, step_seq_id),
    CONSTRAINT uq_prog_name_name UNIQUE      (unit_nbr, step_prog_name)
) STRICT;

CREATE TABLE IF NOT EXISTS dependency_rules (
    unit_nbr     INTEGER NOT NULL,
    rule_id      INTEGER NOT NULL,
    step_seq_id  INTEGER NOT NULL,
    step_dep_id  INTEGER NOT NULL CHECK (step_dep_id >= 0),

    CONSTRAINT pk_dependency_rules      PRIMARY KEY (unit_nbr, rule_id),
    CONSTRAINT uq_dependency_rules_edge UNIQUE      (unit_nbr, step_seq_id, step_dep_id),
    CONSTRAINT ck_dependency_rules_self CHECK (step_seq_id <> step_dep_id),
    -- The dependent step must exist. STEP_DEP_ID cannot carry an FK because of
    -- the 0 sentinel; dangling prerequisites are reported by v_dependency_issues.
    CONSTRAINT fk_dependency_rules_step FOREIGN KEY (unit_nbr, step_seq_id)
        REFERENCES prog_name (unit_nbr, step_seq_id)
) STRICT;

-- The recursive walk joins on the prerequisite side.
CREATE INDEX IF NOT EXISTS ix_dependency_rules_dep
    ON dependency_rules (unit_nbr, step_dep_id, step_seq_id);
