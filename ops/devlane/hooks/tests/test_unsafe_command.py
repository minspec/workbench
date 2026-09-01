"""Moved corpus for unsafe-command.py: refuse, stay silent, see an amend."""

import unittest

import corpus
import support


class UnsafeCommandCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = support.load_claude("unsafe-command.py", "unsafe_command")

    def test_the_moved_tables_are_the_tables_that_were_there(self):
        self.assertEqual(len(corpus.UNSAFE_BLOCK), corpus.COUNTS["UNSAFE_BLOCK"])
        self.assertEqual(len(corpus.UNSAFE_PASS), corpus.COUNTS["UNSAFE_PASS"])
        self.assertEqual(len(corpus.UNSAFE_AMEND), corpus.COUNTS["UNSAFE_AMEND"])
        self.assertEqual(corpus.COUNTS["UNSAFE_BLOCK"], 22)
        self.assertEqual(corpus.COUNTS["UNSAFE_PASS"], 54)
        self.assertEqual(corpus.COUNTS["UNSAFE_AMEND"], 15)

    def test_refuses_each_block_row(self):
        self.assertGreater(len(corpus.UNSAFE_BLOCK), 0)
        for cmd in corpus.UNSAFE_BLOCK:
            with self.subTest(cmd=cmd[:70]):
                hit = self.mod.check(cmd)
                self.assertTrue(
                    hit,
                    f"should refuse, stayed silent: {cmd!r}",
                )

    def test_stays_silent_on_each_pass_row(self):
        self.assertGreater(len(corpus.UNSAFE_PASS), 0)
        for cmd in corpus.UNSAFE_PASS:
            with self.subTest(cmd=cmd[:70]):
                hit = self.mod.check(cmd)
                self.assertFalse(
                    hit,
                    f"false refusal {hit!r} on {cmd!r}",
                )

    def test_amend_detection(self):
        self.assertGreater(len(corpus.UNSAFE_AMEND), 0)
        for cmd, want in corpus.UNSAFE_AMEND:
            with self.subTest(cmd=cmd[:70], want=want):
                self.assertEqual(
                    self.mod.is_amend(cmd), want,
                    f"is_amend({cmd!r}) wanted {want}",
                )

    def test_plan_d3_writing_a_dot_sh_file_is_not_running_it(self):
        """cat > x.sh and tee notes.sh were refused because \\bsh\\b matched the name."""
        rows = [
            """cat > setup.sh <<'EOF'
s = p.read_text()
p.write_text(s.replace(old, new))
EOF""",
            """tee notes.sh <<'EOF'
s = p.read_text()
p.write_text(s.replace(old, new))
EOF""",
        ]
        for cmd in rows:
            with self.subTest(cmd=cmd.splitlines()[0]):
                self.assertIn(cmd, corpus.UNSAFE_PASS)
                self.assertFalse(
                    self.mod.check(cmd),
                    f"writing a .sh file was refused: {cmd!r}",
                )
