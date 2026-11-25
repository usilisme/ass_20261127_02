#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Print the batch execution plan.
#
#   scripts/run.sh                # build a throwaway DB from schema + seed data
#   scripts/run.sh path/to/db     # use an existing DB (views are (re)created)
#
# Exits with 2 and prints the issues to stderr if any unit has an invalid
# dependency graph, so a scheduler never receives a partial or wrong plan.
# -----------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL="$ROOT/sql"

if [[ $# -ge 1 ]]; then
    DB="$1"
    [[ -f "$DB" ]] || { echo "error: database '$DB' not found" >&2; exit 1; }
    sqlite3 -bail "$DB" < "$SQL/02_views.sql"
else
    DB="$(mktemp -t batch_plan.XXXXXX)"
    trap 'rm -f "$DB"' EXIT
    cat "$SQL/01_schema.sql" "$SQL/02_views.sql" "$SQL/03_seed_data.sql" | sqlite3 -bail "$DB"
fi

issues="$(sqlite3 -bail -header -column "$DB" \
    "SELECT * FROM v_dependency_issues ORDER BY unit_nbr, step_seq_id, issue_code;")"
if [[ -n "$issues" ]]; then
    echo "error: invalid dependency rules, no plan produced:" >&2
    echo "$issues" >&2
    exit 2
fi

sqlite3 -bail -header -column "$DB" < "$SQL/04_print_execution_plan.sql"
