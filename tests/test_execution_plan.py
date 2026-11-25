"""
Tests for the batch execution-plan SQL (sql/*.sql).

Standard library only:  python3 -m unittest discover -s tests -v
Every test gets a fresh in-memory SQLite DB built from the real SQL files.
"""

import sqlite3
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"


def load_sql(name: str) -> str:
    return (SQL_DIR / name).read_text()


class DbTestCase(unittest.TestCase):
    """Fresh DB with schema + views; no data unless a test loads it."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)  # autocommit; scripts manage txns
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(load_sql("01_schema.sql"))
        self.db.executescript(load_sql("02_views.sql"))

    def tearDown(self):
        self.db.close()

    # ---- helpers ------------------------------------------------------------
    def add_unit(self, unit_nbr, steps, edges):
        """steps: {seq_id: name}; edges: [(step_seq_id, step_dep_id), ...]"""
        self.db.executemany(
            "INSERT INTO prog_name VALUES (?, ?, ?)",
            [(unit_nbr, sid, name) for sid, name in steps.items()],
        )
        self.db.executemany(
            "INSERT INTO dependency_rules VALUES (?, ?, ?, ?)",
            [(unit_nbr, i, s, d) for i, (s, d) in enumerate(edges, start=1)],
        )

    def chain_unit(self, unit_nbr, n):
        """Linear chain 1 -> 2 -> ... -> n."""
        self.add_unit(
            unit_nbr,
            {i: f"P{unit_nbr}_{i}" for i in range(1, n + 1)},
            [(1, 0)] + [(i, i - 1) for i in range(2, n + 1)],
        )

    def plan(self, unit_nbr=None):
        sql = (load_sql("04_print_execution_plan.sql")
               .replace("FROM   v_execution_plan",
                        "FROM   v_execution_plan WHERE (:u IS NULL OR unit_nbr = :u)"))
        return self.db.execute(sql, {"u": unit_nbr}).fetchall()

    def stages(self, unit_nbr):
        """{step_seq_id: exec_stage} for one unit."""
        return dict(self.db.execute(
            "SELECT step_seq_id, exec_stage FROM v_execution_plan WHERE unit_nbr = ?",
            (unit_nbr,)).fetchall())

    def issues(self, unit_nbr=None):
        return self.db.execute(
            "SELECT unit_nbr, step_seq_id, issue_code FROM v_dependency_issues "
            "WHERE (:u IS NULL OR unit_nbr = :u) ORDER BY 1, 2, 3",
            {"u": unit_nbr}).fetchall()


# =============================================================================
# Case-study data
# =============================================================================
class TestCaseStudy(DbTestCase):

    def setUp(self):
        super().setUp()
        self.db.executescript(load_sql("03_seed_data.sql"))

    def test_seed_data_has_no_issues(self):
        self.assertEqual(self.issues(), [])

    def test_full_plan_matches_expected_order(self):
        expected = [
            (1, 1, "PKGIDS_CMMN_UTILITY.PROCIDS_JOB_START"),
            (1, 2, "pkgids_ptf_hrchy_processing.Procids_delete_job_set_nbr"),
            (1, 3, "PKGIDS_PTF_EXTR.ext_static_ptf_table"),
            (1, 3, "PKGIDS_PTF_EXTR.ext_eff_ptf_table"),
            (1, 4, "pkgids_ptf_hrchy_processing.procids_get_tree_a"),
            (1, 4, "pkgids_ptf_hrchy_processing.procids_get_tree_b"),
            (1, 4, "pkgids_ptf_hrchy_processing.procids_get_tree_c"),
            (1, 4, "pkgids_ptf_hrchy_processing.procids_get_tree_d"),
            (1, 4, "pkgids_ptf_hrchy_processing.procids_get_tree_e"),
            (1, 5, "pkgids_ptf_hrchy_processing.procids_get_active_portf"),
            (1, 6, "pkgids_ptf_lineage.procids_process_ptf_lineage"),
            (1, 7, "pkgids_ptf_lineage.procids_summary_to_bookable_rs"),
            (1, 8, "PKGIDS_CMMN_UTILITY.PROCIDS_JOB_END"),
        ]
        self.assertEqual(self.plan(1), expected)

    def test_parallel_groups(self):
        s = self.stages(1)
        self.assertEqual(s[3], s[4], "steps 3 and 4 both depend only on 2")
        self.assertEqual({s[i] for i in range(5, 10)}, {s[3] + 1}, "steps 5-9 run together")

    def test_every_step_scheduled_exactly_once(self):
        rows = self.db.execute(
            "SELECT step_seq_id, COUNT(*) FROM v_execution_plan "
            "WHERE unit_nbr = 1 GROUP BY step_seq_id").fetchall()
        self.assertEqual(sorted(r[0] for r in rows), list(range(1, 14)))
        self.assertTrue(all(c == 1 for _, c in rows))

    def test_every_step_starts_after_all_prerequisites(self):
        violations = self.db.execute("""
            SELECT r.rule_id
            FROM   dependency_rules r
            JOIN   v_execution_plan s ON s.unit_nbr = r.unit_nbr AND s.step_seq_id = r.step_seq_id
            JOIN   v_execution_plan d ON d.unit_nbr = r.unit_nbr AND d.step_seq_id = r.step_dep_id
            WHERE  s.exec_stage <= d.exec_stage
        """).fetchall()
        self.assertEqual(violations, [])

    def test_run_script_prints_plan(self):
        out = subprocess.run([str(ROOT / "scripts" / "run.sh")],
                             capture_output=True, text=True, check=True).stdout
        lines = [l for l in out.splitlines() if "PKGIDS" in l.upper()]
        self.assertEqual(len(lines), 13)
        self.assertIn("PROCIDS_JOB_START", lines[0])
        self.assertIn("PROCIDS_JOB_END", lines[-1])


# =============================================================================
# Scheduling semantics
# =============================================================================
class TestScheduling(DbTestCase):

    def test_longest_path_wins_over_shortcut(self):
        # 1 -> 2 -> 3 -> 4  and a shortcut 1 -> 4: step 4 must still wait for 3.
        self.add_unit(10, {1: "A", 2: "B", 3: "C", 4: "D"},
                      [(1, 0), (2, 1), (3, 2), (4, 3), (4, 1)])
        self.assertEqual(self.stages(10), {1: 1, 2: 2, 3: 3, 4: 4})

    def test_uneven_branches_join(self):
        # Branch X is 1 step, branch Y is 3 steps; join waits for the longer one.
        self.add_unit(11, {1: "S", 2: "X", 3: "Y1", 4: "Y2", 5: "Y3", 6: "J"},
                      [(1, 0), (2, 1), (3, 1), (4, 3), (5, 4), (6, 2), (6, 5)])
        s = self.stages(11)
        self.assertEqual(s[2], s[3])
        self.assertEqual(s[6], 5)

    def test_multiple_root_steps_start_together(self):
        self.add_unit(12, {1: "R1", 2: "R2", 3: "J"}, [(1, 0), (2, 0), (3, 1), (3, 2)])
        self.assertEqual(self.stages(12), {1: 1, 2: 1, 3: 2})

    def test_redundant_root_marker_does_not_pull_step_forward(self):
        # Step 2 has both "0" and a real prerequisite: it must still wait for 1.
        self.add_unit(13, {1: "A", 2: "B"}, [(1, 0), (2, 0), (2, 1)])
        self.assertEqual(self.stages(13), {1: 1, 2: 2})

    def test_single_step_unit(self):
        self.add_unit(14, {1: "ONLY"}, [(1, 0)])
        self.assertEqual(self.plan(14), [(14, 1, "ONLY")])

    def test_ties_within_stage_ordered_by_step_seq_id(self):
        self.add_unit(15, {1: "S", 4: "D", 2: "B", 3: "C"},
                      [(4, 1), (2, 1), (3, 1), (1, 0)])
        self.assertEqual([r[2] for r in self.plan(15)], ["S", "B", "C", "D"])

    def test_units_are_independent(self):
        # Same step ids in two units must not leak edges across units.
        self.chain_unit(20, 3)
        self.add_unit(21, {1: "A", 2: "B", 3: "C"}, [(1, 0), (2, 1), (3, 1)])
        self.assertEqual(self.stages(20), {1: 1, 2: 2, 3: 3})
        self.assertEqual(self.stages(21), {1: 1, 2: 2, 3: 2})

    def test_unknown_unit_returns_empty_plan(self):
        self.assertEqual(self.plan(999), [])

    def test_long_chain_reaches_full_depth(self):
        # Depth == number of steps is legal and must not be flagged as a cycle.
        self.chain_unit(30, 200)
        self.assertEqual(self.issues(30), [])
        self.assertEqual(self.stages(30)[200], 200)

    def test_wide_dense_dag_is_tractable(self):
        # 10 layers x 6 steps, every step depends on every step of the previous
        # layer => 6^9 (~10M) distinct paths. Path enumeration would not finish;
        # the depth-deduplicated walk must.
        width, layers = 6, 10
        sid = lambda layer, k: layer * width + k + 1
        steps = {sid(l, k): f"L{l}_{k}" for l in range(layers) for k in range(width)}
        edges = [(sid(0, k), 0) for k in range(width)]
        edges += [(sid(l, k), sid(l - 1, j))
                  for l in range(1, layers) for k in range(width) for j in range(width)]
        self.add_unit(40, steps, edges)
        self.assertEqual(self.issues(40), [])
        s = self.stages(40)
        self.assertEqual(len(s), width * layers)
        self.assertEqual(max(s.values()), layers)


# =============================================================================
# Validation of broken dependency graphs
# =============================================================================
class TestValidation(DbTestCase):

    def assert_unit_blocked(self, unit_nbr):
        self.assertEqual(self.plan(unit_nbr), [], "invalid unit must not produce a plan")

    def test_cycle_reachable_from_root(self):
        # 1 -> 2 -> 3 -> 2
        self.add_unit(50, {1: "A", 2: "B", 3: "C"}, [(1, 0), (2, 1), (3, 2), (2, 3)])
        self.assertIn("CYCLE_DETECTED", {i[2] for i in self.issues(50)})
        self.assert_unit_blocked(50)

    def test_isolated_cycle_is_unreachable(self):
        # 2 <-> 3 never connects to the root.
        self.add_unit(51, {1: "A", 2: "B", 3: "C"}, [(1, 0), (2, 3), (3, 2)])
        self.assertEqual(self.issues(51), [(51, 2, "UNREACHABLE_STEP"),
                                           (51, 3, "UNREACHABLE_STEP")])
        self.assert_unit_blocked(51)

    def test_dangling_dependency(self):
        self.add_unit(52, {1: "A", 2: "B"}, [(1, 0), (2, 1), (2, 7)])
        self.assertEqual(self.issues(52), [(52, 2, "DANGLING_DEPENDENCY")])
        self.assert_unit_blocked(52)

    def test_step_without_rule_is_unscheduled(self):
        self.add_unit(53, {1: "A", 2: "B", 3: "ORPHAN"}, [(1, 0), (2, 1)])
        self.assertEqual(self.issues(53), [(53, 3, "UNSCHEDULED_STEP")])
        self.assert_unit_blocked(53)

    def test_no_root_step(self):
        self.add_unit(54, {1: "A", 2: "B"}, [(2, 1)])
        codes = {i[2] for i in self.issues(54)}
        self.assertIn("NO_ROOT_STEP", codes)
        self.assert_unit_blocked(54)

    def test_broken_unit_does_not_block_valid_units(self):
        self.chain_unit(55, 3)
        self.add_unit(56, {1: "A", 2: "B"}, [(1, 0), (2, 9)])
        self.assertEqual(len(self.plan(55)), 3)
        self.assert_unit_blocked(56)

    def test_run_script_fails_on_invalid_rules(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            disk = sqlite3.connect(path, isolation_level=None)
            disk.executescript(load_sql("01_schema.sql"))
            disk.executescript(load_sql("03_seed_data.sql"))
            disk.execute("INSERT INTO dependency_rules VALUES (1, 99, 2, 13)")  # 2 waits for 13 => cycle
            disk.close()
            res = subprocess.run([str(ROOT / "scripts" / "run.sh"), path],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 2)
            self.assertIn("CYCLE_DETECTED", res.stderr)
            self.assertEqual(res.stdout, "")
        finally:
            os.remove(path)


# =============================================================================
# Schema constraints
# =============================================================================
class TestConstraints(DbTestCase):

    def setUp(self):
        super().setUp()
        self.add_unit(60, {1: "A", 2: "B"}, [(1, 0), (2, 1)])

    def assert_rejected(self, sql, params=()):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, params)

    def test_self_dependency_rejected(self):
        self.assert_rejected("INSERT INTO dependency_rules VALUES (60, 10, 2, 2)")

    def test_duplicate_edge_rejected(self):
        self.assert_rejected("INSERT INTO dependency_rules VALUES (60, 10, 2, 1)")

    def test_duplicate_rule_id_rejected(self):
        self.assert_rejected("INSERT INTO dependency_rules VALUES (60, 1, 2, 0)")

    def test_rule_for_unknown_step_rejected(self):
        self.assert_rejected("INSERT INTO dependency_rules VALUES (60, 10, 99, 1)")

    def test_negative_dependency_rejected(self):
        self.assert_rejected("INSERT INTO dependency_rules VALUES (60, 10, 2, -1)")

    def test_step_seq_id_zero_reserved(self):
        self.assert_rejected("INSERT INTO prog_name VALUES (60, 0, 'X')")

    def test_duplicate_prog_name_in_unit_rejected(self):
        self.assert_rejected("INSERT INTO prog_name VALUES (60, 3, 'A')")

    def test_blank_prog_name_rejected(self):
        self.assert_rejected("INSERT INTO prog_name VALUES (60, 3, '   ')")

    def test_strict_typing_rejects_text_ids(self):
        self.assert_rejected("INSERT INTO prog_name VALUES ('x', 3, 'C')")


if __name__ == "__main__":
    unittest.main()
