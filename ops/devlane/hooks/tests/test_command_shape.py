"""Moved corpus for command_shape.py, plus the plant-against-legacy guard.

The legacy split is a verbatim copy of what the three callers shared before
the module existed. A planted row the old split already handles is INVALID,
not a pass.
"""

import re
import unittest

import corpus
import support

# Verbatim from command_shape.py as it shipped: the split the three hooks
# used before this module existed. Kept in the test so a plant the old
# split already handles fails as PLANT NOT LANDED rather than passing.
_LEGACY_SEPARATORS = re.compile(r"[;&|]{1,2}|\$\(|`|\n")
_LEGACY_RUNNERS = re.compile(
    r"^(?:sudo|time|env|nohup|xargs|uv|uvx|npx|poetry|pipenv|poe|pnpm|yarn|npm|bun)\s+"
    r"(?:run\s+)?"
)


def _legacy_commands(text):
    """context-precheck.py:41-47 and boundary-match.py:19-24, as they were."""
    out = []
    for raw in _LEGACY_SEPARATORS.split(text or ""):
        seg = raw.strip()
        while True:
            s = _LEGACY_RUNNERS.sub("", seg, count=1)
            if s == seg:
                break
            seg = s
        if seg:
            out.append(seg)
    return out


def _legacy_statements(text):
    """unsafe-command.py:55,250,272, as it was."""
    return [s.strip() for s in re.split(r";|&&|\|\||\n", text or "") if s.strip()]


class CommandShapeCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = support.load_claude("command_shape.py", "command_shape")

    def test_the_moved_tables_are_the_tables_that_were_there(self):
        self.assertEqual(len(corpus.SHAPE_FINDS), corpus.COUNTS["SHAPE_FINDS"])
        self.assertEqual(len(corpus.SHAPE_REFUSES), corpus.COUNTS["SHAPE_REFUSES"])
        self.assertEqual(len(corpus.SHAPE_KEEPS), corpus.COUNTS["SHAPE_KEEPS"])
        self.assertEqual(len(corpus.SHAPE_PIPELINES), corpus.COUNTS["SHAPE_PIPELINES"])
        self.assertEqual(corpus.COUNTS["SHAPE_FINDS"], 51)
        self.assertEqual(corpus.COUNTS["SHAPE_REFUSES"], 10)
        self.assertEqual(corpus.COUNTS["SHAPE_KEEPS"], 6)
        self.assertEqual(corpus.COUNTS["SHAPE_PIPELINES"], 3)

    def test_finds_each_want_among_command_positions(self):
        self.assertGreater(len(corpus.SHAPE_FINDS), 0)
        for label, text, want, plant in corpus.SHAPE_FINDS:
            with self.subTest(label=label):
                if plant:
                    self.assertNotIn(
                        want, _legacy_commands(text),
                        f"INVALID: PLANT NOT LANDED {label}: "
                        f"the legacy split already finds {want!r}",
                    )
                got = self.mod.commands(text)
                self.assertIn(
                    want, got,
                    f"finds {label}: {want!r} not in {got!r}",
                )

    def test_refuses_to_offer_quoted_or_heredoc_text_as_a_command(self):
        self.assertGreater(len(corpus.SHAPE_REFUSES), 0)
        for label, text, unwanted, plant in corpus.SHAPE_REFUSES:
            with self.subTest(label=label):
                if plant:
                    legacy = [c for c in _legacy_commands(text)
                              if c.startswith(unwanted)]
                    self.assertTrue(
                        legacy,
                        f"INVALID: PLANT NOT LANDED {label}: "
                        f"the legacy split already refuses {unwanted!r}",
                    )
                got = [c for c in self.mod.commands(text)
                       if c.startswith(unwanted)]
                self.assertEqual(
                    got, [],
                    f"refuses {label}: a position still starts "
                    f"{unwanted!r} — {got!r}",
                )

    def test_keeps_heredoc_bodies_that_execute(self):
        self.assertGreater(len(corpus.SHAPE_KEEPS), 0)
        for label, text, body in corpus.SHAPE_KEEPS:
            with self.subTest(label=label):
                self.assertIn(
                    body, text,
                    f"INVALID: PLANT NOT LANDED {label}: "
                    f"body absent from the input",
                )
                kept = self.mod.strip_data_heredocs(text)
                self.assertIn(
                    body, kept,
                    f"keeps {label}: {body!r} was blanked",
                )

    def test_statements_keep_pipelines_whole(self):
        self.assertGreater(len(corpus.SHAPE_PIPELINES), 0)
        for label, text, want_stmts, want_cmds in corpus.SHAPE_PIPELINES:
            with self.subTest(label=label):
                s = self.mod.statements(text)
                c = self.mod.commands(text)
                self.assertEqual(
                    len(s), want_stmts,
                    f"statements {label}: {len(s)} != {want_stmts}  {s!r}",
                )
                self.assertGreaterEqual(
                    len(c), want_cmds,
                    f"commands {label}: {len(c)} < {want_cmds}  {c!r}",
                )

    def test_statements_match_the_legacy_split_where_quoting_is_not_involved(self):
        texts = (
            "git add -A && git commit -q --amend --no-edit",
            "uv run pytest -q 2>&1 | grep -E 'failed|passed' | tail -2",
            "for i in 1 2; do gh api repos/o/r/pulls/1/reviews; sleep 20; done",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(
                    self.mod.statements(text),
                    _legacy_statements(text),
                    f"statements drift on {text!r}",
                )

    def test_strip_data_heredocs_is_idempotent(self):
        hd = "cat > n.md <<'EOF'\ngit push\nEOF"
        once = self.mod.strip_data_heredocs(hd)
        twice = self.mod.strip_data_heredocs(once)
        self.assertEqual(once, twice)

    def test_junk_inputs_return_lists(self):
        for junk in ("", "   ", "'unbalanced quote git push origin b", "$(", "`",
                     "<<", "|||", "cat <<EOF\nno terminator\n", "a" * 5000):
            with self.subTest(junk=junk[:24]):
                self.assertIsInstance(self.mod.commands(junk), list)
                self.assertIsInstance(self.mod.statements(junk), list)

    def test_unbalanced_quoting_falls_back_to_a_blind_split(self):
        text = "'unbalanced quote git push origin b; git push origin b"
        self.assertIn("git push origin b", self.mod.commands(text))
