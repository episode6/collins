# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
"""The reporter: outcomes, the table, the exit status."""

import io
import json
import unittest

from conformance.kit.reporter import FAIL, PASS, SKIP, CheckFailed, CheckSkipped, Reporter


class ReporterTests(unittest.TestCase):
    def make(self) -> tuple[Reporter, io.StringIO]:
        out = io.StringIO()
        return Reporter(out=out), out

    def test_a_check_that_returns_passes_and_its_value_comes_back(self):
        reporter, out = self.make()
        reporter.group("g")
        value = reporter.run("a", lambda notes: 42)
        self.assertEqual(value, 42)
        self.assertEqual([r.outcome for r in reporter.results], [PASS])
        self.assertIn("ok   g: a", out.getvalue())
        self.assertEqual(reporter.exit_code(), 0)

    def test_check_failed_is_a_fail_with_its_detail(self):
        reporter, out = self.make()
        reporter.group("g")

        def check(notes):
            raise CheckFailed("saw 1, expected 2")

        self.assertIsNone(reporter.run("b", check))
        self.assertEqual(reporter.results[0].outcome, FAIL)
        self.assertEqual(reporter.results[0].detail, "saw 1, expected 2")
        self.assertIn("FAIL g: b -- saw 1, expected 2", out.getvalue())
        self.assertEqual(reporter.exit_code(), 1)

    def test_check_skipped_is_a_skip_and_does_not_fail_the_run(self):
        reporter, _ = self.make()

        def check(notes):
            raise CheckSkipped("not claimed")

        reporter.run("c", check)
        reporter.skip("d", "left out")
        self.assertEqual([r.outcome for r in reporter.results], [SKIP, SKIP])
        self.assertEqual(reporter.exit_code(), 0)
        self.assertEqual(reporter.skipped, 2)

    def test_any_other_exception_is_a_fail_naming_the_error(self):
        reporter, _ = self.make()

        def check(notes):
            raise KeyError("pty")

        reporter.run("e", check)
        self.assertEqual(reporter.results[0].outcome, FAIL)
        self.assertIn("KeyError", reporter.results[0].detail)

    def test_notes_are_printed_and_kept(self):
        reporter, out = self.make()

        def check(notes):
            notes.append("measured 3 ms")

        reporter.run("f", check)
        self.assertEqual(reporter.results[0].notes, ["measured 3 ms"])
        self.assertIn("note: measured 3 ms", out.getvalue())

    def test_the_table_and_the_json(self):
        reporter, _ = self.make()
        reporter.group("level 0")
        reporter.run("hello", lambda notes: None)
        reporter.run("bad", lambda notes: (_ for _ in ()).throw(CheckFailed("no")))
        table = reporter.table()
        self.assertIn("pass level 0: hello", table)
        self.assertIn("fail level 0: bad", table)
        self.assertIn("1 passed, 1 failed, 0 skipped", table)
        rows = json.loads(reporter.to_json())
        self.assertEqual([r["outcome"] for r in rows], [PASS, FAIL])
        self.assertEqual(rows[1]["detail"], "no")


if __name__ == "__main__":
    unittest.main()
