"""Moved corpus for test-guard.py: each case is a file that must or must not flag.

The case bodies are the two real bugs this tool exists to catch, and the
files that must stay quiet. They live here as triple-quoted data so
scanning THIS file does not treat the examples as plants.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import support

# Copied from test-guard.py's in-file table. 10 rows: 4 that must flag,
# 6 that must not.
CASES = [
    ("pr-body-check-test.sh", """
clean; sed -i 's/^## Checklist$/## Checklis/' "$TMP/body.md"
[ "$(fired checklist)" != "0" ] && ok "catches it" || bad "catches it"
""", True),

    ("body-test.sh", """
clean
python3 - "$F" <<'PY'
import sys
p = sys.argv[1]
open(p, "wb").write(open(p, "rb").read().replace(b"\\n", b"\\r\\n"))
PY
""", True),

    ("thing_test.py", """
def test_it(tmp):
    p = tmp / "f.md"
    p.write_text(p.read_text().replace("good", "bad"))
    assert check(p) != 0
""", True),

    # PROSE DOCUMENTING the anti-pattern is not the anti-pattern.
    ("doctrine.md", """
| too much happened | `open(p,"w").write(open(p,"r").read())` — the write truncates before
the read runs | the fixture is emptied, so the fault isn't there either |
""", False),

    # ...but the same text in something that actually runs is still a finding
    ("fixup.sh", """
python3 -c 'open(p,"w").write(open(p,"r").read())'
""", True),

    # guarded: the fixed harness
    ("pr-body-check-test.sh", """
plant() {
    before=$(cksum < "$BODY"); "$1"; after=$(cksum < "$BODY")
    [ "$before" = "$after" ] && { bad "PLANT DID NOT APPLY"; return 1; }
}
clean; plant mut_x && sed -i 's/^## Checklist$/## Checklis/' "$BODY"
""", False),

    # selftest.sh's shape: literal whole-file fixtures and appends
    ("selftest.sh", """
printf '[claim]\\nlabel = true\\nrun = echo yes\\n' > cl/ok.txt
for i in 1 2 3; do echo "x$i" >> src/untouched.py; done
""", False),

    # a test that mutates but proves the mutation with a content assertion
    ("edit_test.py", """
def test_edit(tmp):
    p = tmp / "f.py"
    p.write_text(p.read_text().replace("a", "b"))
    assert "b" in p.read_text()
    assert check(p) != 0
""", False),

    # not a test file: a release script rewriting a version is not a plant
    ("bump-version.sh", """
sed -i "s/^version = .*/version = \\"$NEW\\"/" pyproject.toml
""", False),

    # the pattern named in a comment, not executed
    ("notes-test.sh", """
# never write `sed -i` without checking the anchor matched
printf 'literal\\n' > "$F"
""", False),
]


class TestGuardCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = support.load_claude("test-guard.py", "test_guard")

    def setUp(self):
        # The scanner treats a path as a test file when the PATH matches
        # a test-ish regex. A tempdir named `test-…` would make a
        # release script look like a test. The original --test used
        # tempfile's `tmp*` prefix, which does not match.
        self.tmp = Path(tempfile.mkdtemp(prefix="tg-corpus-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_moved_tables_are_the_tables_that_were_there(self):
        self.assertEqual(len(CASES), 10)
        must_flag = sum(1 for _, _, w in CASES if w)
        must_not = sum(1 for _, _, w in CASES if not w)
        self.assertEqual(must_flag, 4)
        self.assertEqual(must_not, 6)

    def test_each_case(self):
        self.assertGreater(len(CASES), 0)
        for i, (name, body, want) in enumerate(CASES):
            with self.subTest(name=name, want=want, i=i):
                path = self.tmp / f"{i}_{name}"
                support.plant_text(path, body, recognisable=body.strip()[:12])
                landed = path.read_text(encoding="utf-8")
                self.assertEqual(landed, body, "INVALID: plant did not land")
                self.assertNotEqual(landed, "", "INVALID: plant emptied the file")
                got = bool(self.mod.scan(path))
                self.assertEqual(
                    got, want,
                    f"{'MISSED' if want else 'NOISE'}: {name} "
                    f"(first line {body.strip().splitlines()[0][:60]!r})",
                )
