# Batch Job Execution Plan (SQLite)

Reads `PROG_NAME` and `DEPENDENCY_RULES` and prints each batch job's
stored procedures (`UNIT_NBR`, `STEP_PROG_NAME`) in the order they may run.

## Run

```bash
scripts/run.sh               # throwaway DB built from schema + case-study data
scripts/run.sh my.db         # an existing DB that already has the two tables
python3 -m unittest discover -s tests -v    # 32 tests, stdlib only
```

Needs `sqlite3` ≥ 3.37 (STRICT tables) and Python 3 for the tests.

## Layout

| File | Purpose |
|------|---------|
| `sql/01_schema.sql` | Tables with PK / UNIQUE / CHECK / FK constraints, plus an index on the dependency side |
| `sql/02_views.sql` | `v_step_depth`, `v_dependency_issues`, `v_execution_plan` (the core logic) |
| `sql/03_seed_data.sql` | Case-study data for UNIT_NBR 1 |
| `sql/04_print_execution_plan.sql` | The final ordered report |
| `scripts/run.sh` | Validates first (exit 2 + issues on stderr), then prints the plan |
| `tests/test_execution_plan.py` | Unit tests for the plan, edge cases, validation and constraints |

## Approach

A step's **EXEC_STAGE** is the length of the **longest** dependency chain from a
root step (`STEP_DEP_ID = 0`). So a step starts only after *all* of its
prerequisites have finished, and steps that share a stage can run in parallel.
Within a stage, steps are listed by `STEP_SEQ_ID`.

Taking the longest chain matters. With `1→2→3→4` plus a shortcut `1→4`, the
shortest chain would wrongly put step 4 at stage 2, before step 3. The
case-study data has no such shortcuts, so a shortest-path solution would also
pass on it. The extra tests cover this case.

The recursive CTE uses `UNION` over `(unit, step, depth)` instead of building
paths. That keeps the work polynomial: a 60-step dense DAG with about 10M
distinct paths finishes in milliseconds. The walk also stops at depth N+1
(N = number of steps in the unit), so cycles always end and can be detected.

## Validation (`v_dependency_issues`)

| Code | Meaning |
|------|---------|
| `DANGLING_DEPENDENCY` | `STEP_DEP_ID` is not a step in `PROG_NAME` |
| `NO_ROOT_STEP` | The unit has no rule with `STEP_DEP_ID = 0` |
| `UNSCHEDULED_STEP` | A step in `PROG_NAME` has no dependency rule |
| `UNREACHABLE_STEP` | A step cannot be reached from a root (for example, an isolated cycle) |
| `CYCLE_DETECTED` | A step is on, or downstream of, a circular dependency |

A unit with any issue is left out of `v_execution_plan`, so a broken plan is
never run. Other valid units are not affected.

## Result for UNIT_NBR 1

| Stage | Steps (run in parallel) |
|------:|------|
| 1 | PROCIDS_JOB_START |
| 2 | Procids_delete_job_set_nbr |
| 3 | ext_static_ptf_table, ext_eff_ptf_table |
| 4 | procids_get_tree_a … procids_get_tree_e |
| 5 | procids_get_active_portf |
| 6 | procids_process_ptf_lineage |
| 7 | procids_summary_to_bookable_rs |
| 8 | PROCIDS_JOB_END |

## Gaps and Improvements
There are validations that are added as per above to handle the exception cases:
1. Steps do not exist in the master PROG_NAME
2. The Batch Unit does not have a root step.
3. Step(s) are defined with no dependency, so it does not execute.
4. Step(s) cannot be reached from the root, so it does not execute.
5. Step(s) are in a cyclical graph.

### Airflow
Considering using Airflow DAGs in the data pipelines architecture to structure and validate any of these in the source codes instead of relying on SQL.