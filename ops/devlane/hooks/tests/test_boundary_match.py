"""Moved corpus for boundary-match.py."""

import unittest

import corpus
import support


class BoundaryMatchCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = support.load_claude("boundary-match.py", "boundary_match")

    def test_the_moved_tables_are_the_tables_that_were_there(self):
        self.assertEqual(len(corpus.BOUNDARY_FIRE),
                         corpus.COUNTS["BOUNDARY_FIRE"])
        self.assertEqual(len(corpus.BOUNDARY_SILENT),
                         corpus.COUNTS["BOUNDARY_SILENT"])
        self.assertEqual(corpus.COUNTS["BOUNDARY_FIRE"], 29)
        self.assertEqual(corpus.COUNTS["BOUNDARY_SILENT"], 37)

    def test_fires_on_each_fire_row(self):
        self.assertGreater(len(corpus.BOUNDARY_FIRE), 0)
        for cmd in corpus.BOUNDARY_FIRE:
            with self.subTest(cmd=cmd):
                hit = self.mod.classify(cmd)
                self.assertTrue(
                    hit,
                    f"should fire, stayed silent: {cmd!r}",
                )

    def test_stays_silent_on_each_silent_row(self):
        self.assertGreater(len(corpus.BOUNDARY_SILENT), 0)
        for cmd in corpus.BOUNDARY_SILENT:
            with self.subTest(cmd=cmd):
                hit = self.mod.classify(cmd)
                self.assertFalse(
                    hit,
                    f"false positive {hit!r} on {cmd!r}",
                )

    def test_plan_d3_git_dash_c_checkout_is_a_tree_crossing(self):
        rows = [
            "git -C sub checkout main",
            "git -C sub worktree add ../wt",
        ]
        for cmd in rows:
            with self.subTest(cmd=cmd):
                hit = self.mod.classify(cmd)
                self.assertTrue(hit, f"D3 form was silent: {cmd!r}")
                self.assertEqual(hit[0], "tree", f"{cmd!r} fired as {hit!r}")

    def test_plan_d3_data_heredoc_is_not_a_tree_crossing(self):
        rows = [
            "cat > mem.md <<'EOF'\nNote:\ngit checkout main is a boundary\nEOF",
            "cat > notes.md <<'EOF'\nMake one with:\ngit worktree add ../wt\nEOF",
            'echo "done; git checkout main"',
        ]
        for cmd in rows:
            with self.subTest(cmd=cmd[:40]):
                self.assertFalse(
                    self.mod.classify(cmd),
                    f"prose was a tree crossing: {cmd!r}",
                )
