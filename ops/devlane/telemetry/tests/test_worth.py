from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve()
APP = HERE.parents[2]
WORTH = HERE.parents[1] / "worth.py"
STORES_PATH = APP / "fixtures" / "stores.py"

SPEC = importlib.util.spec_from_file_location("worth_test_stores", STORES_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load fixture API from {STORES_PATH}")
STORES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORES)

NOW = "2026-08-22T12:00:00.000Z"
DEFAULT_SINCE = "2026-08-21T12:00:00.000Z"
WINDOW_SINCE = "2026-08-22T12:00:00.000Z"
WINDOW_UNTIL = "2026-08-22T12:05:00.000Z"


def iso_epoch(value):
    return datetime.fromisoformat(value).timestamp()


def git_date(value):
    stamp = datetime.fromisoformat(value)
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def canonical_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def json_lines(path):
    return [
        json.loads(raw)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]


def walk_leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_leaves(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_leaves(child, path + (str(index),))
    else:
        yield path, value


def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


class WorthContractTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="worth-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()

        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.grok = self.root / "grok"

        self._git("init", "-q", "-b", "dev")
        self._git("config", "user.name", "Fixture Owner")
        self._git("config", "user.email", "fixture@example.invalid")
        self.initial = self._commit_files(
            {"README.md": "throwaway worth fixture\n"},
            "initial fixture",
            "2026-08-20T10:00:00.000Z",
        )

    def _git_env(self, when=None):
        env = os.environ.copy()
        env.update({
            "LC_ALL": "C",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        if when is not None:
            fixed = git_date(when)
            env["GIT_AUTHOR_DATE"] = fixed
            env["GIT_COMMITTER_DATE"] = fixed
        return env

    def _git(self, *args, when=None, check=True):
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            env=self._git_env(when),
            capture_output=True,
            text=True,
            check=False,
        )
        if check:
            self.assertEqual(
                proc.returncode,
                0,
                f"git {' '.join(args)} failed\nstdout:\n{proc.stdout}"
                f"\nstderr:\n{proc.stderr}",
            )
        return proc

    def _write_repo_file(self, relative, content):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.assertEqual(path.read_text(encoding="utf-8"), content)
        return path

    def _assert_commit(self, sha, subject, when, parents=None):
        record = self._git(
            "show",
            "-s",
            "--format=%H%x00%s%x00%aI%x00%cI%x00%P",
            sha,
        ).stdout.rstrip("\n").split("\x00")
        self.assertEqual(record[0], sha)
        self.assertEqual(record[1], subject)
        # git 2.51 prints UTC %aI/%cI with a Z suffix, older gits
        # with +00:00 — compare instants, not spellings
        for observed in (record[2], record[3]):
            self.assertEqual(
                datetime.fromisoformat(observed),
                datetime.fromisoformat(git_date(when)),
            )
        if parents is not None:
            self.assertEqual(record[4].split(), list(parents))
        self.assertEqual(self._git("cat-file", "-t", sha).stdout.strip(), "commit")

    def _commit_files(self, files, subject, when):
        for relative, content in files.items():
            self._write_repo_file(relative, content)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", subject, when=when)
        sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._assert_commit(sha, subject, when)
        for relative, content in files.items():
            landed = self._git("show", f"{sha}:{relative}").stdout
            self.assertEqual(landed, content)
        return sha

    def _merge(self, branch, subject, when):
        first_parent = self._git("rev-parse", "HEAD").stdout.strip()
        second_parent = self._git("rev-parse", branch).stdout.strip()
        self._git("merge", "-q", "--no-ff", "-m", subject, branch, when=when)
        sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._assert_commit(
            sha,
            subject,
            when,
            parents=(first_parent, second_parent),
        )
        self.assertEqual(
            self._git("merge-base", "--is-ancestor", second_parent, sha).returncode,
            0,
        )
        return sha

    def _slug(self, repo=None):
        repo = str(repo or self.repo)
        return "-" + "-".join(repo.strip("/").split("/"))

    def _write_jsonl(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
            encoding="utf-8",
        )
        planted = json_lines(path)
        self.assertEqual(len(planted), len(entries))
        self.assertEqual(planted, entries)
        return path

    def _plant_claude(self, session_id, messages, repo=None, marker=None):
        repo = str(repo or self.repo)
        marker = marker or f"marker-{session_id}"
        entries = []
        for index, message in enumerate(messages, 1):
            usage = {
                "input_tokens": message.get("input", 0),
                "cache_creation_input_tokens": message.get(
                    "cache_creation", 0
                ),
                "cache_read_input_tokens": message.get("cache_read", 0),
                "output_tokens": message.get("output", 0),
            }
            entries.append(STORES.claude_entry(
                timestamp=message["timestamp"],
                cwd=repo,
                session_id=session_id,
                model=message.get("model", "claude-fable-5"),
                effort="high",
                mid=message.get("id", f"{session_id}-msg-{index}"),
                usage=usage,
                content=[{"type": "text", "text": marker}],
            ))
        path = self.claude / self._slug(repo) / f"{session_id}.jsonl"
        self._write_jsonl(path, entries)
        planted = json_lines(path)
        self.assertEqual(
            [row["message"]["id"] for row in planted],
            [row["message"]["id"] for row in entries],
        )
        self.assertTrue(all(marker in json.dumps(row) for row in planted))
        return path

    def _plant_standard_claude(
        self,
        session_id,
        base,
        repo=None,
        replacement_usage=None,
    ):
        repo = str(repo or self.repo)
        marker = f"standard-marker-{session_id}"
        STORES.build_claude_store(
            self.claude,
            self._slug(repo),
            base_timestamp=iso_epoch(base),
            cwd=repo,
            session_id=session_id,
            model="claude-fable-5",
            effort="high",
            marker=marker,
            reemit_last=replacement_usage is not None,
        )
        path = self.claude / self._slug(repo) / f"{session_id}.jsonl"
        planted = json_lines(path)
        expected_count = 3 if replacement_usage is not None else 2
        self.assertEqual(len(planted), expected_count)
        self.assertTrue(all(marker in json.dumps(row) for row in planted))
        if replacement_usage is not None:
            planted[-1]["timestamp"] = replacement_usage["timestamp"]
            planted[-1]["message"]["usage"] = {
                "input_tokens": replacement_usage.get("input", 0),
                "cache_creation_input_tokens": replacement_usage.get(
                    "cache_creation", 0
                ),
                "cache_read_input_tokens": replacement_usage.get(
                    "cache_read", 0
                ),
                "output_tokens": replacement_usage.get("output", 0),
            }
            self._write_jsonl(path, planted)
            reread = json_lines(path)
            self.assertEqual(
                reread[-1]["message"]["id"],
                reread[-2]["message"]["id"],
                "the planted replacement must exercise last-wins by id",
            )
            self.assertEqual(
                reread[-1]["message"]["usage"],
                planted[-1]["message"]["usage"],
            )
        return path

    def _plant_standard_codex(
        self,
        session_id,
        base,
        repo=None,
        last_usage=None,
    ):
        repo = str(repo or self.repo)
        marker = f"standard-marker-{session_id}"
        STORES.build_codex_store(
            self.codex,
            base_timestamp=iso_epoch(base),
            cwd=repo,
            session_id=session_id,
            model="gpt-5-codex",
            effort="high",
            marker=marker,
        )
        matches = list(self.codex.rglob(f"*{session_id}.jsonl"))
        self.assertEqual(len(matches), 1)
        path = matches[0]
        planted = json_lines(path)
        self.assertEqual(len(planted), 5)
        self.assertEqual(planted[0]["payload"]["id"], session_id)
        self.assertIn(marker, path.read_text(encoding="utf-8"))
        token_rows = [
            row for row in planted
            if (row.get("payload") or {}).get("type") == "token_count"
        ]
        self.assertEqual(len(token_rows), 2)
        if last_usage is not None:
            token_rows[-1]["payload"]["info"]["total_token_usage"] = last_usage
            token_index = max(
                index for index, row in enumerate(planted)
                if (row.get("payload") or {}).get("type") == "token_count"
            )
            planted[token_index] = token_rows[-1]
            self._write_jsonl(path, planted)
            reread = json_lines(path)
            self.assertEqual(
                reread[token_index]["payload"]["info"]["total_token_usage"],
                last_usage,
            )
        return path

    def _plant_grok(self, session_id, base, usage_runs=None, repo=None):
        repo = str(repo or self.repo)
        marker = f"standard-marker-{session_id}"
        usage_runs = usage_runs or []
        STORES.build_grok_store(
            self.grok,
            repo,
            base_timestamp=iso_epoch(base),
            session_id=session_id,
            model="grok-4.6",
            marker=marker,
            usage_runs=usage_runs,
        )
        session = (
            self.grok / "sessions" / quote(repo, safe="") / session_id
        )
        summary = json.loads(
            (session / "summary.json").read_text(encoding="utf-8")
        )
        updates = json_lines(session / "updates.jsonl")
        events = json_lines(session / "events.jsonl")
        self.assertEqual(summary["info"]["id"], session_id)
        self.assertEqual(summary["info"]["cwd"], repo)
        self.assertIn(marker, json.dumps(summary))
        self.assertEqual(len(updates), 2 + sum(map(len, usage_runs)))
        self.assertEqual(len(events), 7)
        planted_usage = [
            row["params"]["update"]["usage"]
            for row in updates
            if row["params"]["update"].get("sessionUpdate") == "turn_completed"
        ]
        self.assertEqual(
            planted_usage,
            [usage for run in usage_runs for usage in run],
        )
        return session

    def _run_worth(self, verb, *args, now=NOW, repo=None):
        repo = str(repo or self.repo)
        cmd = [
            sys.executable,
            str(WORTH),
            verb,
            *args,
            "--now",
            now,
            "--repo",
            repo,
            "--claude-dir",
            str(self.claude),
            "--codex-dir",
            str(self.codex),
            "--grok-dir",
            str(self.grok),
        ]
        self.assertEqual(cmd.count("--now"), 1)
        return subprocess.run(
            cmd,
            cwd=self.root,
            env={
                **os.environ,
                "LC_ALL": "C",
                "TZ": "Etc/GMT+11",
                "HOME": str(self.root / "empty-home"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def _json_worth(self, verb, *args, now=NOW, repo=None):
        proc = self._run_worth(
            verb,
            *args,
            "--format",
            "json",
            now=now,
            repo=repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        try:
            value = json.loads(proc.stdout)
        except ValueError as exc:
            self.fail(f"invalid JSON: {exc}\nstdout:\n{proc.stdout}")
        self.assertIsInstance(value, dict)
        return value

    def _harness_record(self, data, harness):
        container = data["cost"]
        wanted = canonical_key(harness)

        def find(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if canonical_key(key) == wanted:
                        return child
                if canonical_key(value.get("harness", "")) == wanted:
                    return value
                for child in value.values():
                    found = find(child)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find(child)
                    if found is not None:
                        return found
            return None

        record = find(container)
        self.assertIsNotNone(record, f"no cost record for {harness}: {container}")
        return record

    def _line_for_harness(self, plain, harness):
        lines = [
            line for line in plain.splitlines()
            if re.search(rf"\b{re.escape(harness)}\b", line, re.IGNORECASE)
        ]
        self.assertTrue(lines, f"no plain-format line for {harness}:\n{plain}")
        return lines[0]

    def _assert_key_number(self, value, aliases, expected):
        wanted = {canonical_key(alias) for alias in aliases}
        matches = [
            leaf for path, leaf in walk_leaves(value)
            if path
            and canonical_key(path[-1]) in wanted
            and isinstance(leaf, (int, float))
            and not isinstance(leaf, bool)
        ]
        self.assertIn(
            expected,
            matches,
            f"expected {aliases}={expected}; observed {matches} in {value}",
        )

    def _assert_plain_number(self, line, aliases, expected):
        names = "|".join(re.escape(alias) for alias in aliases)
        self.assertRegex(
            line,
            rf"\b(?:{names})\s*[=:]\s*{re.escape(str(expected))}\b",
        )

    def _session_ids(self, data):
        sessions = data["sessions"]
        self.assertIsInstance(sessions, list)

        ids = []
        for row in sessions:
            self.assertIsInstance(row, dict)
            found = None
            for path, value in walk_leaves(row):
                if (
                    path
                    and canonical_key(path[-1]) in {"session", "sessionid", "id"}
                    and isinstance(value, str)
                ):
                    found = value
                    break
            self.assertIsNotNone(found, f"ranked session lacks an id: {row}")
            ids.append(found)
        return ids

    def _signal_kind(self, signal):
        for path, value in walk_leaves(signal):
            if (
                path
                and canonical_key(path[-1]) in {"kind", "signal", "type"}
                and isinstance(value, str)
            ):
                return value
        rendered = json.dumps(signal, sort_keys=True)
        for kind in ("cache-churn", "heavy-turn"):
            if kind in rendered:
                return kind
        return None

    def _signal_field(self, signal, aliases):
        wanted = {canonical_key(alias) for alias in aliases}
        for path, value in walk_leaves(signal):
            if path and canonical_key(path[-1]) in wanted:
                return value
        return None

    def _signals_of_kind(self, data, kind):
        signals = data["signals"]
        self.assertIsInstance(signals, list)
        return [
            signal for signal in signals
            if self._signal_kind(signal) == kind
        ]

    def _assert_signal(
        self,
        data,
        kind,
        harness,
        session_id,
        required_values=(),
    ):
        matches = []
        for signal in self._signals_of_kind(data, kind):
            if (
                self._signal_field(signal, ("harness",)) == harness
                and self._signal_field(
                    signal, ("session", "session_id", "id")
                ) == session_id
            ):
                matches.append(signal)
        self.assertEqual(
            len(matches),
            1,
            f"expected one {kind} for {harness}/{session_id}: {data['signals']}",
        )
        rendered = json.dumps(matches[0], sort_keys=True)
        for value in required_values:
            self.assertIn(str(value), rendered)
        return matches[0]

    def _assert_stamp(self, plain, data, since, until, now):
        self.assertIn("stamp", data)
        stamp = json.dumps(data["stamp"], sort_keys=True)
        head = self._git("rev-parse", "--short", "HEAD").stdout.strip()
        for expected in (head, "dev", since, until, now):
            self.assertIn(expected, stamp)
            self.assertIn(expected, plain)
        self.assertRegex(
            plain,
            rf"\[{re.escape(since)}\s*,\s*{re.escape(until)}\)",
        )

    def _assert_plain_json_parity(self, plain, data):
        for path, value in walk_leaves(data):
            if value is None:
                self.assertIn("unrecorded", plain.lower())
            elif isinstance(value, bool):
                if value and path:
                    self.assertIn(path[-1].replace("_", "-"), plain.lower())
            elif value != "":
                self.assertIn(
                    str(value),
                    plain,
                    f"JSON-only value at {'.'.join(path)}: {value!r}",
                )

        rendered = json.dumps(data, sort_keys=True)
        for label, figure in re.findall(
            r"\b([A-Za-z][A-Za-z0-9_-]*)\s*=\s*"
            r"(unrecorded|-?\d+(?:/\d+)?)\b",
            plain,
        ):
            self.assertIn(label.replace("-", "").replace("_", "").lower(),
                          canonical_key(rendered))
            if figure != "unrecorded":
                for part in figure.split("/"):
                    self.assertIn(part, rendered)

        for stamp in re.findall(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?"
            r"(?:Z|[+-]\d\d:\d\d)",
            plain,
        ):
            self.assertIn(stamp, rendered)
        for pr in re.findall(r"#(\d+)", plain):
            self.assertIn(pr, rendered)
        for sha in re.findall(r"\b[0-9a-f]{7,40}\b", plain):
            self.assertIn(sha, rendered)

    def _pr_numbers(self, results):
        found = set()

        def visit(value, in_pr=False):
            if isinstance(value, dict):
                for key, child in value.items():
                    key_name = canonical_key(key)
                    child_in_pr = in_pr or key_name in {
                        "pr",
                        "prs",
                        "prnumber",
                        "prnumbers",
                        "pullrequest",
                        "pullrequests",
                    }
                    visit(child, child_in_pr)
            elif isinstance(value, list):
                for child in value:
                    visit(child, in_pr)
            elif in_pr:
                if isinstance(value, int) and not isinstance(value, bool):
                    found.add(value)
                elif isinstance(value, str):
                    for number in re.findall(
                        r"(?:pull request\s*)?#?(\d+)", value, re.IGNORECASE
                    ):
                        found.add(int(number))

        visit(results)
        return found

    def _assert_result_measure_zero(self, results, fragments):
        fragments = tuple(canonical_key(item) for item in fragments)
        candidates = []
        for node in walk_dicts(results):
            for key, value in node.items():
                name = canonical_key(key)
                if any(
                    name == fragment
                    or name.startswith(fragment)
                    or fragment in name
                    for fragment in fragments
                ):
                    candidates.append(value)

        def contains_zero(value):
            if value == 0 or value == []:
                return True
            if isinstance(value, dict):
                return any(contains_zero(child) for child in value.values())
            if isinstance(value, list):
                return len(value) == 0 or any(
                    contains_zero(child) for child in value
                )
            return False

        self.assertTrue(
            any(contains_zero(value) for value in candidates),
            f"no zero-valued result measure for {fragments}: {results}",
        )

    def test_default_window_boundaries_and_independent_edge_overrides(self):
        self._plant_claude("boundary-session", [
            {
                "id": "below-lower",
                "timestamp": "2026-08-21T11:59:59.000Z",
                "output": 1,
            },
            {
                "id": "at-lower",
                "timestamp": DEFAULT_SINCE,
                "output": 2,
            },
            {
                "id": "at-early-until",
                "timestamp": "2026-08-21T13:00:00.000Z",
                "output": 16,
            },
            {
                "id": "inside-upper",
                "timestamp": "2026-08-22T11:59:59.000Z",
                "output": 4,
            },
            {
                "id": "at-upper",
                "timestamp": NOW,
                "output": 8,
            },
        ])

        default = self._json_worth("report")
        claude = self._harness_record(default, "claude")
        self._assert_key_number(claude, ("messages",), 3)
        self._assert_key_number(claude, ("output", "out"), 22)

        since_only = self._json_worth(
            "report",
            "--since",
            "2026-08-22T00:00:00.000Z",
        )
        claude = self._harness_record(since_only, "claude")
        self._assert_key_number(claude, ("messages",), 1)
        self._assert_key_number(claude, ("output", "out"), 4)

        until_only = self._json_worth(
            "report",
            "--until",
            "2026-08-21T13:00:00.000Z",
        )
        claude = self._harness_record(until_only, "claude")
        self._assert_key_number(claude, ("messages",), 1)
        self._assert_key_number(claude, ("output", "out"), 2)

    def test_claude_and_codex_window_per_message_but_grok_windows_per_run(self):
        # The standard store's second message rides 300s after base;
        # this base puts it at 12:00:10 — inside [12:00:00, 12:00:15)
        # — while the first stays outside, which is the straddle the
        # expected figures (msg2 alone) always described. The authored
        # base of 11:59:00 left BOTH messages outside the window.
        self._plant_standard_claude(
            "claude-straddle",
            "2026-08-22T11:55:10.000Z",
        )
        self._plant_standard_codex(
            "codex-straddle",
            "2026-08-22T11:59:00.000Z",
        )

        included_run = [[
            {
                "inputTokens": 60,
                "cachedReadTokens": 20,
                "cacheCreationTokens": 5,
                "outputTokens": 15,
                "totalTokens": 100,
                "reasoningTokens": 3,
                "costUsdTicks": 10,
            },
            {
                "inputTokens": 120,
                "cachedReadTokens": 40,
                "cacheCreationTokens": 10,
                "outputTokens": 30,
                "totalTokens": 200,
                "reasoningTokens": 6,
                "costUsdTicks": 20,
            },
        ]]
        excluded_run = [[
            {
                "inputTokens": 1,
                "cachedReadTokens": 2,
                "cacheCreationTokens": 3,
                "outputTokens": 40_000,
                "totalTokens": 40_006,
                "reasoningTokens": 4,
                "costUsdTicks": 40_010,
            },
            {
                "inputTokens": 2,
                "cachedReadTokens": 3,
                "cacheCreationTokens": 4,
                "outputTokens": 90_000,
                "totalTokens": 90_009,
                "reasoningTokens": 5,
                "costUsdTicks": 90_010,
            },
        ]]
        self._plant_grok(
            "grok-last-inside",
            "2026-08-22T11:58:00.000Z",
            included_run,
        )
        self._plant_grok(
            "grok-last-outside",
            "2026-08-22T11:58:30.000Z",
            excluded_run,
        )

        report = self._json_worth(
            "report",
            "--since",
            WINDOW_SINCE,
            "--until",
            "2026-08-22T12:00:15.000Z",
            now="2026-08-22T13:00:00.000Z",
        )

        claude = self._harness_record(report, "claude")
        self._assert_key_number(claude, ("sessions",), 1)
        self._assert_key_number(claude, ("messages",), 1)
        self._assert_key_number(claude, ("input", "in"), 20)
        self._assert_key_number(claude, ("cached",), 6_000)
        self._assert_key_number(claude, ("output", "out"), 300)
        self._assert_key_number(claude, ("total",), 6_320)

        codex = self._harness_record(report, "codex")
        self._assert_key_number(codex, ("sessions",), 1)
        self._assert_key_number(codex, ("messages",), 1)
        self._assert_key_number(codex, ("input", "in"), 200)
        self._assert_key_number(codex, ("cached",), 100)
        self._assert_key_number(codex, ("output", "out"), 40)
        self._assert_key_number(codex, ("total",), 240)

        grok = self._harness_record(report, "grok")
        self._assert_key_number(grok, ("sessions",), 1)
        self._assert_key_number(grok, ("runs",), 1)
        self._assert_key_number(grok, ("input", "in"), 120)
        self._assert_key_number(grok, ("cached",), 50)
        self._assert_key_number(grok, ("output", "out"), 30)
        self._assert_key_number(grok, ("total",), 200)
        self.assertNotIn("messages", canonical_key(json.dumps(grok)))
        self.assertNotIn("90000", json.dumps(report))

        waste = self._json_worth(
            "waste",
            "--since",
            WINDOW_SINCE,
            "--until",
            "2026-08-22T12:00:15.000Z",
            "--top",
            "10",
            now="2026-08-22T13:00:00.000Z",
        )
        ids = self._session_ids(waste)
        self.assertIn("claude-straddle", ids)
        self.assertIn("codex-straddle", ids)
        self.assertIn("grok-last-inside", ids)
        self.assertNotIn("grok-last-outside", ids)

    def test_report_reuses_usage_accounting_and_preserves_measurement_gaps(self):
        self._plant_standard_claude(
            "claude-accounting",
            "2026-08-22T10:00:00.000Z",
            replacement_usage={
                "timestamp": "2026-08-22T10:10:00.000Z",
                "input": 7,
                "cache_creation": 11,
                "cache_read": 13,
                "output": 17,
            },
        )
        self._plant_standard_codex(
            "codex-accounting",
            "2026-08-22T10:00:00.000Z",
        )

        grok_runs = [
            [
                {
                    "inputTokens": 60,
                    "cachedReadTokens": 20,
                    "cacheCreationTokens": 5,
                    "outputTokens": 15,
                    "totalTokens": 100,
                    "reasoningTokens": 3,
                    "costUsdTicks": 10,
                },
                {
                    "inputTokens": 120,
                    "cachedReadTokens": 40,
                    "cacheCreationTokens": 10,
                    "outputTokens": 30,
                    "totalTokens": 200,
                    "reasoningTokens": 6,
                    "costUsdTicks": 20,
                },
            ],
            [
                {
                    "inputTokens": 30,
                    "cachedReadTokens": 10,
                    "cacheCreationTokens": 2,
                    "outputTokens": 8,
                    "totalTokens": 50,
                    "reasoningTokens": 1,
                    "costUsdTicks": 5,
                },
                {
                    "inputTokens": 45,
                    "cachedReadTokens": 15,
                    "cacheCreationTokens": 3,
                    "outputTokens": 17,
                    "totalTokens": 80,
                    "reasoningTokens": 2,
                    "costUsdTicks": 8,
                    "usageIsIncomplete": True,
                },
            ],
        ]
        self._plant_grok(
            "grok-accounting",
            "2026-08-22T10:00:00.000Z",
            grok_runs,
        )
        self._plant_grok(
            "grok-unrecorded",
            "2026-08-22T10:30:00.000Z",
            [],
        )

        other_repo = self.root / "other-repo"
        other_repo.mkdir()
        self.assertTrue(other_repo.is_dir())
        self._plant_claude(
            "other-claude",
            [{
                "timestamp": "2026-08-22T10:00:00.000Z",
                "input": 888_001,
                "cache_read": 888_002,
                "output": 888_003,
            }],
            repo=other_repo,
        )
        self._plant_standard_codex(
            "other-codex",
            "2026-08-22T10:00:00.000Z",
            repo=other_repo,
            last_usage={
                "input_tokens": 777_001,
                "cached_input_tokens": 777_002,
                "output_tokens": 777_003,
                "reasoning_output_tokens": 777_004,
                "total_tokens": 777_005,
            },
        )
        self._plant_grok(
            "other-grok",
            "2026-08-22T10:00:00.000Z",
            [[{
                "inputTokens": 999_001,
                "cachedReadTokens": 999_002,
                "cacheCreationTokens": 999_003,
                "outputTokens": 999_004,
                "totalTokens": 999_005,
                "reasoningTokens": 999_006,
                "costUsdTicks": 999_007,
            }]],
            repo=other_repo,
        )

        plain_proc = self._run_worth("report")
        self.assertEqual(plain_proc.returncode, 0, plain_proc.stderr)
        data = self._json_worth("report")

        claude = self._harness_record(data, "claude")
        self._assert_key_number(claude, ("sessions",), 1)
        self._assert_key_number(claude, ("messages",), 2)
        self._assert_key_number(claude, ("input", "in"), 17)
        self._assert_key_number(claude, ("cached",), 6_024)
        self._assert_key_number(claude, ("output", "out"), 217)
        self._assert_key_number(claude, ("total",), 6_258)

        codex = self._harness_record(data, "codex")
        self._assert_key_number(codex, ("sessions",), 1)
        self._assert_key_number(codex, ("messages",), 2)
        self._assert_key_number(codex, ("input", "in"), 400)
        self._assert_key_number(codex, ("cached",), 300)
        self._assert_key_number(codex, ("output", "out"), 90)
        self._assert_key_number(codex, ("total",), 490)

        grok = self._harness_record(data, "grok")
        self._assert_key_number(grok, ("sessions",), 2)
        self._assert_key_number(grok, ("runs",), 2)
        self._assert_key_number(grok, ("input", "in"), 165)
        self._assert_key_number(grok, ("cached",), 68)
        self._assert_key_number(grok, ("output", "out"), 47)
        self._assert_key_number(grok, ("total",), 280)
        self._assert_key_number(
            grok,
            ("cost_usd_ticks", "costUsdTicks"),
            28,
        )

        grok_rendered = json.dumps(grok, sort_keys=True).lower()
        self.assertIn("counted", grok_rendered)
        self.assertTrue(
            "1/2" in grok_rendered
            or (
                any(
                    value == 1
                    for path, value in walk_leaves(grok)
                    if path and canonical_key(path[-1]) == "counted"
                )
                and any(
                    value == 2
                    for path, value in walk_leaves(grok)
                    if path and canonical_key(path[-1]) == "sessions"
                )
            )
        )
        self.assertTrue(
            any(
                bool(value)
                for path, value in walk_leaves(grok)
                if path and "incomplete" in canonical_key(path[-1])
            ),
            f"incompleteness missing from JSON: {grok}",
        )

        claude_line = self._line_for_harness(plain_proc.stdout, "claude")
        self._assert_plain_number(claude_line, ("messages",), 2)
        self._assert_plain_number(claude_line, ("in", "input"), 17)
        self._assert_plain_number(claude_line, ("cached",), 6_024)
        self._assert_plain_number(claude_line, ("out", "output"), 217)

        codex_line = self._line_for_harness(plain_proc.stdout, "codex")
        self._assert_plain_number(codex_line, ("messages",), 2)
        self._assert_plain_number(codex_line, ("total",), 490)

        grok_line = self._line_for_harness(plain_proc.stdout, "grok")
        self._assert_plain_number(grok_line, ("runs",), 2)
        self.assertIn("counted=1/2", grok_line)
        self.assertIn("(incomplete)", grok_line)
        self._assert_plain_number(
            grok_line,
            ("cost_usd_ticks", "cost-ticks"),
            28,
        )

        combined = plain_proc.stdout + json.dumps(data, sort_keys=True)
        for forbidden in (
            "other-claude",
            "other-codex",
            "other-grok",
            "888001",
            "777005",
            "999007",
        ):
            self.assertNotIn(forbidden, combined)

    def test_absent_and_unparseable_stores_are_unrecorded_never_zero(self):
        """Scenario: an absent store is unrecorded, never zero"""
        absent_plain = self._run_worth("report")
        self.assertEqual(absent_plain.returncode, 0, absent_plain.stderr)
        absent_json = self._json_worth("report")

        for harness in ("claude", "codex", "grok"):
            record = self._harness_record(absent_json, harness)
            self.assertIn("unrecorded", json.dumps(record).lower())
            line = self._line_for_harness(absent_plain.stdout, harness)
            self.assertIn("unrecorded", line.lower())
            self.assertNotRegex(
                line,
                r"\b(?:tokens?|in|input|cached|out|output|total|"
                r"cost(?:_usd_ticks)?)\s*[=:]\s*0\b",
            )

        malformed_claude = (
            self.claude / self._slug() / "malformed-claude.jsonl"
        )
        malformed_claude.parent.mkdir(parents=True, exist_ok=True)
        malformed_claude.write_text("{not-json\n", encoding="utf-8")
        self.assertEqual(
            malformed_claude.read_text(encoding="utf-8"),
            "{not-json\n",
        )
        self.assertEqual(len(malformed_claude.read_text().splitlines()), 1)

        malformed_codex = (
            self.codex / "sessions" / "2026" / "08" / "22"
            / "rollout-malformed-codex.jsonl"
        )
        malformed_codex.parent.mkdir(parents=True, exist_ok=True)
        malformed_codex.write_text("[not-json\n", encoding="utf-8")
        self.assertEqual(
            malformed_codex.read_text(encoding="utf-8"),
            "[not-json\n",
        )
        self.assertEqual(len(malformed_codex.read_text().splitlines()), 1)

        malformed_grok = (
            self.grok / "sessions" / quote(str(self.repo), safe="")
            / "malformed-grok"
        )
        malformed_grok.mkdir(parents=True)
        (malformed_grok / "summary.json").write_text(
            "not-json",
            encoding="utf-8",
        )
        self.assertEqual(
            (malformed_grok / "summary.json").read_text(encoding="utf-8"),
            "not-json",
        )
        self.assertEqual(
            len(list(malformed_grok.iterdir())),
            1,
            "the malformed Grok fixture did not land as planted",
        )

        malformed_plain = self._run_worth("report")
        self.assertEqual(malformed_plain.returncode, 0, malformed_plain.stderr)
        malformed_json = self._json_worth("report")

        for harness in ("claude", "codex", "grok"):
            record = self._harness_record(malformed_json, harness)
            self.assertIn("unrecorded", json.dumps(record).lower())
            line = self._line_for_harness(malformed_plain.stdout, harness)
            self.assertIn("unrecorded", line.lower())
            self.assertNotRegex(
                line,
                r"\b(?:tokens?|in|input|cached|out|output|total|"
                r"cost(?:_usd_ticks)?)\s*[=:]\s*0\b",
            )

    def test_results_use_first_parent_git_history_and_edge_trees_only(self):
        one_test = (
            "#[test]\n"
            "fn baseline_definition() {}\n"
        )
        two_tests = (
            one_test
            + "\n#[test]\n"
            "fn pr_definition() {}\n"
        )
        three_tests = (
            two_tests
            + "\n#[test]\n"
            "fn integration_definition() {}\n"
        )
        four_tests = (
            three_tests
            + "\n#[test]\n"
            "fn uncommitted_definition() {}\n"
        )

        self._commit_files(
            {"tests/contract.rs": one_test},
            "plant baseline test definition",
            "2026-08-21T10:00:00.000Z",
        )

        self._git("switch", "-q", "-c", "pr-41")
        pr_commit = self._commit_files(
            {
                "tests/contract.rs": two_tests,
                "src/pr41.txt": "numbered merge content\n",
            },
            "implement numbered fixture",
            "2026-08-21T13:00:00.000Z",
        )
        self._git("switch", "-q", "dev")
        numbered_merge = self._merge(
            "pr-41",
            "Merge pull request #41 from fixtures/pr-41",
            "2026-08-21T13:30:00.000Z",
        )
        self.assertEqual(
            self._git("show", f"{numbered_merge}:tests/contract.rs").stdout,
            two_tests,
        )
        self.assertEqual(
            self._git("merge-base", "--is-ancestor", pr_commit, numbered_merge)
            .returncode,
            0,
        )

        self._git("switch", "-q", "-c", "integration")
        integration_commit = self._commit_files(
            {
                "tests/contract.rs": three_tests,
                "src/integration.txt": "unnumbered merge content\n",
            },
            "implement integration fixture",
            "2026-08-21T14:00:00.000Z",
        )
        self._git("switch", "-q", "-c", "nested")
        nested_commit = self._commit_files(
            {"src/nested.txt": "second-parent-only merge content\n"},
            "implement nested fixture",
            "2026-08-21T14:10:00.000Z",
        )
        self._git("switch", "-q", "integration")
        hidden_merge = self._merge(
            "nested",
            "Merge pull request #999 from fixtures/nested",
            "2026-08-21T14:20:00.000Z",
        )
        self._git("switch", "-q", "dev")
        unnumbered_merge = self._merge(
            "integration",
            "Merge integration branch",
            "2026-08-21T14:30:00.000Z",
        )
        self.assertEqual(
            self._git("show", f"{unnumbered_merge}:tests/contract.rs").stdout,
            three_tests,
        )
        for landed in (integration_commit, nested_commit, hidden_merge):
            self.assertEqual(
                self._git(
                    "merge-base", "--is-ancestor", landed, unnumbered_merge
                ).returncode,
                0,
            )

        self._git("switch", "-q", "-c", "pr-88")
        outside_commit = self._commit_files(
            {"src/outside.txt": "exclusive-upper-bound content\n"},
            "implement outside fixture",
            "2026-08-22T12:00:00.000Z",
        )
        self._git("switch", "-q", "dev")
        outside_merge = self._merge(
            "pr-88",
            "Merge pull request #88 from fixtures/pr-88",
            "2026-08-22T12:00:00.000Z",
        )
        self.assertEqual(
            self._git(
                "merge-base", "--is-ancestor", outside_commit, outside_merge
            ).returncode,
            0,
        )

        self._write_repo_file("tests/contract.rs", four_tests)
        self.assertEqual(
            len(re.findall(r"(?m)^\s*#\[test\]\s*$", four_tests)),
            4,
        )
        self.assertEqual(
            len(re.findall(
                r"(?m)^\s*#\[test\]\s*$",
                self._git("show", "HEAD:tests/contract.rs").stdout,
            )),
            3,
            "the uncommitted fourth test must not be in the HEAD tree",
        )

        self._plant_claude(
            "not-a-git-result",
            [{
                "timestamp": "2026-08-21T15:00:00.000Z",
                "output": 1,
            }],
            marker="Merge pull request #777 from a transcript",
        )

        args = (
            "--since",
            DEFAULT_SINCE,
            "--until",
            "2026-08-22T12:00:00.000Z",
        )
        plain_proc = self._run_worth("report", *args)
        self.assertEqual(plain_proc.returncode, 0, plain_proc.stderr)
        data = self._json_worth("report", *args)
        results = data["results"]
        rendered = json.dumps(results, sort_keys=True)

        self.assertEqual(self._pr_numbers(results), {41})
        self.assertEqual(set(map(int, re.findall(r"#(\d+)", plain_proc.stdout))),
                         {41})
        for forbidden in ("#88", "#999", "#777"):
            self.assertNotIn(forbidden, plain_proc.stdout)
            self.assertNotIn(forbidden, rendered)

        identity = (
            unnumbered_merge[:7],
            "Merge integration branch",
        )
        self.assertTrue(
            any(value in plain_proc.stdout for value in identity),
            "the unnumbered first-parent merge was dropped from plain output",
        )
        self.assertTrue(
            any(value in rendered for value in identity),
            "the unnumbered first-parent merge was dropped from JSON",
        )

        test_values = [
            value for path, value in walk_leaves(results)
            if any("test" in canonical_key(part) for part in path)
        ]
        self.assertIn(2, test_values)
        self.assertRegex(
            plain_proc.stdout,
            r"(?i)test[^\n]*(?:delta\s*[=:]\s*)?\+?2\b",
        )
        self.assertNotIn("uncommitted_definition", plain_proc.stdout)
        self.assertNotIn("uncommitted_definition", rendered)

    def test_missing_window_edge_commit_makes_test_delta_unrecorded(self):
        since = "2026-08-20T09:00:00.000Z"
        until = "2026-08-20T11:00:00.000Z"
        plain = self._run_worth(
            "report",
            "--since",
            since,
            "--until",
            until,
            now="2026-08-20T12:00:00.000Z",
        )
        self.assertEqual(plain.returncode, 0, plain.stderr)
        data = self._json_worth(
            "report",
            "--since",
            since,
            "--until",
            until,
            now="2026-08-20T12:00:00.000Z",
        )
        test_gap_values = [
            value for path, value in walk_leaves(data["results"])
            if any("test" in canonical_key(part) for part in path)
        ]
        self.assertTrue(
            any(
                isinstance(value, str)
                and value.lower() == "unrecorded"
                for value in test_gap_values
            ),
            f"test delta did not preserve the missing-edge gap: {data['results']}",
        )
        self.assertRegex(
            plain.stdout,
            r"(?i)test[^\n]*unrecorded",
        )

    def test_stamp_is_identical_in_plain_and_json_for_both_subcommands(self):
        since = "2026-08-21T18:00:00.000Z"
        until = "2026-08-22T06:00:00.000Z"
        now = "2026-08-22T07:00:00.000Z"

        for verb in ("report", "waste"):
            with self.subTest(verb=verb):
                plain = self._run_worth(
                    verb,
                    "--since",
                    since,
                    "--until",
                    until,
                    now=now,
                )
                self.assertEqual(plain.returncode, 0, plain.stderr)
                data = self._json_worth(
                    verb,
                    "--since",
                    since,
                    "--until",
                    until,
                    now=now,
                )
                self._assert_stamp(plain.stdout, data, since, until, now)

    def test_waste_ranks_window_totals_honors_top_and_breaks_ties_by_id(self):
        inside = "2026-08-22T11:00:00.000Z"
        outside = "2026-08-21T11:00:00.000Z"
        totals = {
            "leader": 300,
            "tie-a": 200,
            "tie-b": 200,
            "third": 150,
            "fourth": 120,
            "fifth": 100,
        }
        for session_id, total in totals.items():
            self._plant_claude(session_id, [{
                "timestamp": inside,
                "output": total,
            }])

        self._plant_claude("mixed-window", [
            {
                "id": "mixed-outside",
                "timestamp": outside,
                "output": 100_000,
            },
            {
                "id": "mixed-inside",
                "timestamp": inside,
                "output": 10,
            },
        ])

        expected_default = [
            "leader",
            "tie-a",
            "tie-b",
            "third",
            "fourth",
        ]
        default_json = self._json_worth("waste")
        self.assertEqual(self._session_ids(default_json), expected_default)

        default_plain = self._run_worth("waste")
        self.assertEqual(default_plain.returncode, 0, default_plain.stderr)
        positions = [
            default_plain.stdout.find(session_id)
            for session_id in expected_default
        ]
        self.assertTrue(all(position >= 0 for position in positions))
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("fifth", default_plain.stdout)
        self.assertNotIn("mixed-window", default_plain.stdout)
        self.assertNotIn("100000", default_plain.stdout)
        self.assertNotIn("100000", json.dumps(default_json))

        top_three = self._json_worth("waste", "--top", "3")
        self.assertEqual(
            self._session_ids(top_three),
            ["leader", "tie-a", "tie-b"],
        )

        top_two = self._json_worth("waste", "--top", "2")
        self.assertEqual(
            self._session_ids(top_two),
            ["leader", "tie-a"],
        )

    def test_waste_emits_deterministic_cache_churn_and_heavy_turn_signals(self):
        self._plant_claude("claude-heavy", [
            {
                "id": "claude-heavy-small",
                "timestamp": "2026-08-22T10:00:00.000Z",
                "output": 5,
            },
            {
                "id": "claude-heavy-big",
                "timestamp": "2026-08-22T10:01:00.000Z",
                "output": 40,
            },
        ])
        self._plant_standard_codex(
            "codex-heavy",
            "2026-08-22T10:00:00.000Z",
        )
        self._plant_grok(
            "grok-heavy",
            "2026-08-22T10:00:00.000Z",
            [
                [{
                    "inputTokens": 470,
                    "cachedReadTokens": 0,
                    "cacheCreationTokens": 0,
                    "outputTokens": 30,
                    "totalTokens": 500,
                    "reasoningTokens": 0,
                    "costUsdTicks": 5,
                }],
                [{
                    "inputTokens": 50,
                    "cachedReadTokens": 0,
                    "cacheCreationTokens": 0,
                    "outputTokens": 50,
                    "totalTokens": 100,
                    "reasoningTokens": 0,
                    "costUsdTicks": 2,
                }],
            ],
        )

        self._plant_claude("churn-positive", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "cache_read": 201,
            "output": 10,
        }])
        self._plant_claude("churn-equal", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "cache_read": 200,
            "output": 10,
        }])
        self._plant_claude("churn-zero-output", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "cache_read": 1_000,
            "output": 0,
        }])
        self._plant_claude("churn-creation-only", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "cache_creation": 1_000,
            "cache_read": 0,
            "output": 10,
        }])

        data = self._json_worth("waste", "--top", "20")
        plain = self._run_worth("waste", "--top", "20")
        self.assertEqual(plain.returncode, 0, plain.stderr)

        cache_signals = self._signals_of_kind(data, "cache-churn")
        cache_sessions = {
            self._signal_field(signal, ("session", "session_id", "id"))
            for signal in cache_signals
        }
        self.assertIn("churn-positive", cache_sessions)
        self.assertNotIn("churn-equal", cache_sessions)
        self.assertNotIn("churn-zero-output", cache_sessions)
        self.assertNotIn("churn-creation-only", cache_sessions)
        self._assert_signal(
            data,
            "cache-churn",
            "claude",
            "churn-positive",
            required_values=(201, 10),
        )

        self._assert_signal(
            data,
            "heavy-turn",
            "claude",
            "claude-heavy",
            required_values=("claude-heavy-big", 40),
        )
        self._assert_signal(
            data,
            "heavy-turn",
            "codex",
            "codex-heavy",
            required_values=("2026-08-22T10:09:00.000Z", 90),
        )
        self._assert_signal(
            data,
            "heavy-turn",
            "grok",
            "grok-heavy",
            required_values=(50,),
        )

        ranked_ids = set(self._session_ids(data))
        for signal in data["signals"]:
            harness = self._signal_field(signal, ("harness",))
            session_id = self._signal_field(
                signal, ("session", "session_id", "id")
            )
            self.assertIn(harness, {"claude", "codex", "grok"})
            self.assertIn(session_id, ranked_ids)

        for expected in (
            "cache-churn",
            "heavy-turn",
            "claude",
            "codex",
            "grok",
            "churn-positive",
            "claude-heavy",
            "codex-heavy",
            "grok-heavy",
        ):
            self.assertIn(expected, plain.stdout)

    def test_json_and_plain_are_two_encodings_of_the_same_truth(self):
        self._plant_standard_claude(
            "parity-claude",
            "2026-08-22T10:00:00.000Z",
        )
        self._plant_grok(
            "parity-grok",
            "2026-08-22T10:00:00.000Z",
            [[{
                "inputTokens": 70,
                "cachedReadTokens": 10,
                "cacheCreationTokens": 5,
                "outputTokens": 15,
                "totalTokens": 100,
                "reasoningTokens": 2,
                "costUsdTicks": 9,
            }]],
        )

        self._git("switch", "-q", "-c", "pr-61")
        self._commit_files(
            {"src/parity.txt": "parity merge content\n"},
            "implement parity fixture",
            "2026-08-22T10:30:00.000Z",
        )
        self._git("switch", "-q", "dev")
        self._merge(
            "pr-61",
            "Merge pull request #61 from fixtures/parity",
            "2026-08-22T10:40:00.000Z",
        )

        for verb, keys in (
            ("report", {"stamp", "cost", "results"}),
            ("waste", {"stamp", "sessions", "signals"}),
        ):
            with self.subTest(verb=verb):
                plain = self._run_worth(verb)
                self.assertEqual(plain.returncode, 0, plain.stderr)
                data = self._json_worth(verb)
                self.assertEqual(set(data), keys)
                self._assert_plain_json_parity(plain.stdout, data)

    def test_empty_windows_succeed_with_zero_results_and_zero_sessions(self):
        since = "2026-09-01T12:00:00.000Z"
        until = "2026-09-02T12:00:00.000Z"
        now = until

        report_plain = self._run_worth(
            "report",
            "--since",
            since,
            "--until",
            until,
            now=now,
        )
        self.assertEqual(report_plain.returncode, 0, report_plain.stderr)
        report_json = self._json_worth(
            "report",
            "--since",
            since,
            "--until",
            until,
            now=now,
        )

        self._assert_result_measure_zero(
            report_json["results"],
            ("pr", "prs", "pullrequest"),
        )
        self._assert_result_measure_zero(
            report_json["results"],
            ("commit", "commits"),
        )
        self._assert_result_measure_zero(
            report_json["results"],
            ("test", "tests", "testdefinitions"),
        )
        self.assertRegex(
            report_plain.stdout,
            r"(?i)\bprs?\s*[=:]\s*0\b",
        )
        self.assertRegex(
            report_plain.stdout,
            r"(?i)\bcommits?\s*[=:]\s*0\b",
        )
        self.assertRegex(
            report_plain.stdout,
            r"(?i)\btest(?:s|[-_ ]definitions?)?"
            r"(?:[-_ ]delta)?\s*[=:]\s*\+?0\b",
        )

        for harness in ("claude", "codex", "grok"):
            record = self._harness_record(report_json, harness)
            self._assert_key_number(record, ("sessions",), 0)
            line = self._line_for_harness(report_plain.stdout, harness)
            self._assert_plain_number(line, ("sessions",), 0)

        waste_plain = self._run_worth(
            "waste",
            "--since",
            since,
            "--until",
            until,
            now=now,
        )
        self.assertEqual(waste_plain.returncode, 0, waste_plain.stderr)
        waste_json = self._json_worth(
            "waste",
            "--since",
            since,
            "--until",
            until,
            now=now,
        )
        self.assertEqual(waste_json["sessions"], [])
        self.assertEqual(waste_json["signals"], [])
        for harness in ("claude", "codex", "grok"):
            line = self._line_for_harness(waste_plain.stdout, harness)
            self._assert_plain_number(line, ("sessions",), 0)

    def test_unusable_arguments_exit_two_and_explain_the_reason(self):
        cases = [
            (
                "report",
                ("--since", "not-an-iso"),
                NOW,
                "since",
            ),
            (
                "waste",
                (
                    "--since",
                    "2026-08-22T12:00:00.000Z",
                    "--until",
                    "2026-08-22T12:00:00.000Z",
                ),
                "2026-08-22T13:00:00.000Z",
                "until",
            ),
            (
                "report",
                ("--format", "yaml"),
                NOW,
                "format",
            ),
            (
                "waste",
                (),
                "not-an-iso",
                "now",
            ),
        ]
        for verb, args, now, reason in cases:
            with self.subTest(verb=verb, args=args, now=now):
                proc = self._run_worth(verb, *args, now=now)
                self.assertEqual(proc.returncode, 2, proc)
                self.assertTrue(proc.stderr.strip())
                self.assertIn(reason, proc.stderr.lower())


if __name__ == "__main__":
    unittest.main()
