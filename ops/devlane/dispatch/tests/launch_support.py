"""Shared fixtures for the launch.py / record.py suite.

Import boundary (RED design): launch.py and record.py are absent at
the contract-only head. Tests never import them at collection time —
that would be a collection error, which is not a red. Each test loads
through ``require_module``. When the file is missing, that helper
returns an empty-stub stand-in whose ``main`` / ``build`` / ``validate``
do nothing useful, so the test fails on the contracted assertion, not
on "file absent". An empty ``launch.py`` / ``record.py`` on disk fails
the same way. A skip that later flips to a behavioural red is not used.

No test here reads a wall clock for a result. Stream timestamps come
from stores.py with a caller-chosen epoch. Fake harness CLIs always
write a start-witness (path baked into the script, not an env var the
launcher can strip) so a refusal can prove the child did not run.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

import support

APP = support.APP
REPO = support.REPO
HARNESS_DIR = APP.parents[0] / "harness"
FIXTURES_DIR = APP.parents[0] / "fixtures"
STORES_PATH = FIXTURES_DIR / "stores.py"
WIRES_PATH = HARNESS_DIR / "wires.py"
WALL_PY = REPO / "ops" / "devlane" / "workflow" / "checks" / "vocabulary_wall.py"
# jobs.json is owned by the task app; dispatch reads it by path.
JOBS_PATH = APP.parents[0] / "task" / "jobs.json"

AGENT = "Launch Test <noreply@example.invalid>"
REFUSAL_EXIT = 3
STREAM_EPOCH = 1_700_000_000
REQUESTED_MODEL = "alias-requested"
RAN_MODEL = "stream-ran-model"
SCOPE_CAP = 1024
DEFAULT_GRACE = 120

# Id form from CONTRACT.md §Dispatch: <UTC stamp>-<stage>-<harness>-<6 hex>
ID_RE = re.compile(
    r"^\d{8}T\d{6}Z-"
    r"(plan|tests|check-tests|code|review|adjudicate)-"
    r"(claude|codex|grok)-[0-9a-f]{6}$"
)

RECORD_FIELDS = (
    "id", "lane", "stage", "unit", "lineage", "follows", "job", "role",
    "dispatched_by", "at", "snapshot", "harness", "model", "session",
    "brief", "caps", "overrides", "attempts", "result", "status",
)

# A subprocess harness. Records argv/cwd/env/stdin/prompt, optionally
# writes a harness-shaped stream, optionally mutates the snapshot.
# The start-witness path is interpolated at install time so a launcher
# that unsets TASK_LAUNCH_WITNESS still leaves evidence it ran.
# No network. No clock read — stream epochs come from the environment.
_FAKE_CLI = r"""#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

start_witness = Path(@@START_WITNESS@@)
ran_model = @@RAN_MODEL@@
stores_path = @@STORES_PATH@@
done_path = os.environ.get("TASK_LAUNCH_DONE")
sleep_s = float(os.environ.get("TASK_LAUNCH_SLEEP") or "0")
stdout_mode = os.environ.get("TASK_LAUNCH_STDOUT") or "envelope"
token = os.environ.get("TASK_LAUNCH_TOKEN") or ""
job = os.environ.get("TASK_LAUNCH_JOB") or "plan"
verdict = os.environ.get("TASK_LAUNCH_VERDICT") or "approve"
status = os.environ.get("TASK_LAUNCH_STATUS") or "ok"
envelope_commit = os.environ.get("TASK_LAUNCH_ENVELOPE_COMMIT") or ""
epoch = float(os.environ.get("TASK_LAUNCH_STREAM_EPOCH") or "1700000000")
write_stream = os.environ.get("TASK_LAUNCH_WRITE_STREAM") == "1"
ignore_session = os.environ.get("TASK_LAUNCH_IGNORE_SESSION") == "1"
stream_id_override = os.environ.get("TASK_LAUNCH_STREAM_ID") or ""
commit_rel = os.environ.get("TASK_LAUNCH_COMMIT") or ""
edit_rel = os.environ.get("TASK_LAUNCH_EDIT") or ""
orphan = os.environ.get("TASK_LAUNCH_ORPHAN") == "1"
head_commit = os.environ.get("TASK_LAUNCH_HEAD_COMMIT") or ""
exit_code = int(os.environ.get("TASK_LAUNCH_EXIT") or "0")
over_out = int(os.environ.get("TASK_LAUNCH_OVER_OUT") or "0")
grandchild_path = os.environ.get("TASK_LAUNCH_GRANDCHILD") or ""
witness = os.environ.get("TASK_LAUNCH_WITNESS")

exe = Path(sys.argv[0]).name if sys.argv else ""
argv = sys.argv[1:]
prompt_file = None
if "--prompt-file" in argv:
    idx = argv.index("--prompt-file")
    if idx + 1 < len(argv):
        prompt_file = argv[idx + 1]

session_id = ""
if "--session-id" in argv:
    idx = argv.index("--session-id")
    if idx + 1 < len(argv):
        session_id = argv[idx + 1]
if "-s" in argv:
    idx = argv.index("-s")
    if idx + 1 < len(argv):
        session_id = argv[idx + 1]

stdin_data = sys.stdin.read()
prompt_text = ""
if prompt_file:
    try:
        prompt_text = Path(prompt_file).read_text(encoding="utf-8")
    except OSError:
        prompt_text = ""

env_keys = [
    "CLICOLOR_FORCE", "FORCE_COLOR", "NO_COLOR", "CLICOLOR", "TERM",
    "PAGER", "GH_PAGER", "GIT_PAGER", "LESS", "CI", "GIT_TERMINAL_PROMPT",
    "GIT_EDITOR", "EDITOR", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    "LC_ALL", "WF_LANE", "DISPATCH_JOB", "CODEX_HOME", "GROK_HOME",
    "HOME", "CLAUDE_CONFIG_DIR", "WF_AGENT",
]
env_shot = {k: os.environ[k] for k in env_keys if k in os.environ}

job_parent = Path(os.getcwd()).parent
last_message = None
if "-o" in argv:
    oidx = argv.index("-o")
    if oidx + 1 < len(argv):
        last_message = Path(argv[oidx + 1])
if last_message is None:
    last_message = job_parent / "out" / "last-message.json"

def _schema_probe():
    if "--json-schema" not in argv:
        return False, None, None, "missing"
    sidx = argv.index("--json-schema")
    if sidx + 1 >= len(argv):
        return False, None, None, "no-value"
    value = argv[sidx + 1]
    if str(value).lstrip().startswith("{"):
        try:
            json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False, None, None, "inline-invalid"
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        return True, None, digest, "inline"
    path = Path(value)
    try:
        text = path.read_text(encoding="utf-8")
        json.loads(text)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False, str(path), None, "unreadable"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return True, str(path), digest, "file"

schema_read, schema_path, schema_sha, schema_how = _schema_probe()

try:
    pgid = os.getpgid(0)
except OSError:
    pgid = os.getpid()
start_witness.parent.mkdir(parents=True, exist_ok=True)
start_witness.write_text(json.dumps({
    "pid": os.getpid(),
    "pgid": pgid,
    "argv": sys.argv,
    "cwd": os.getcwd(),
    "exe": sys.argv[0] if sys.argv else "",
    "session_id": session_id,
    "exit_present": (job_parent / "exit").exists(),
    "tripped_present": (job_parent / "TRIPPED.md").exists(),
    "last_message_present": last_message.exists(),
    "schema_read": schema_read,
    "schema_path": schema_path,
    "schema_sha": schema_sha,
    "schema_how": schema_how,
}) + "\n", encoding="utf-8")

if witness:
    Path(witness).write_text(json.dumps({
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "stdin": stdin_data,
        "prompt_file": prompt_file,
        "prompt_text": prompt_text,
        "env": env_shot,
        "session_id": session_id,
        "exe": sys.argv[0] if sys.argv else "",
        "schema_read": schema_read,
        "schema_path": schema_path,
        "schema_sha": schema_sha,
        "schema_how": schema_how,
        "last_message_present": last_message.exists(),
    }), encoding="utf-8")

if grandchild_path:
    child_pid = os.fork()
    if child_pid == 0:
        time.sleep(max(sleep_s, 30))
        os._exit(0)
    Path(grandchild_path).write_text(str(child_pid) + "\n", encoding="utf-8")

stream_id = stream_id_override or session_id or "00000000-0000-4000-8000-000000000001"
if ignore_session:
    stream_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"

def git(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_AUTHOR_NAME": "worker",
        "GIT_AUTHOR_EMAIL": "worker@example.test",
        "GIT_COMMITTER_NAME": "worker",
        "GIT_COMMITTER_EMAIL": "worker@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return subprocess.run(
        ["git", *args], cwd=os.getcwd(), env=env,
        capture_output=True, text=True,
    )

if commit_rel:
    p = Path(commit_rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("worker-commit\n", encoding="utf-8")
    git("add", "--", commit_rel)
    git("commit", "-m", "worker: change the snapshot")

if edit_rel:
    p = Path(edit_rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("worker-edit-uncommitted\n", encoding="utf-8")

if orphan:
    tree = git("write-tree")
    tree_sha = tree.stdout.strip()
    made = git("commit-tree", tree_sha, "-m", "orphan head")
    sha = made.stdout.strip()
    if sha:
        git("reset", "--soft", sha)

if write_stream and stores_path:
    spec = importlib.util.spec_from_file_location("task_launch_stores", stores_path)
    stores = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stores)
    cwd = os.getcwd()
    if exe == "claude":
        home = os.environ.get("HOME") or ""
        root = Path(home) / ".claude" / "projects"
        slug = cwd.replace("/", "-").replace(".", "-")
        stores.build_claude_store(
            root, slug, base_timestamp=epoch, cwd=cwd,
            session_id=stream_id, model=ran_model, effort="high",
            marker="LAUNCH-FAKE",
        )
    elif exe == "codex":
        home = os.environ.get("CODEX_HOME") or str(Path(os.environ.get("HOME", "")) / ".codex")
        stores.build_codex_store(
            Path(home), base_timestamp=epoch, cwd=cwd,
            session_id=stream_id, model=ran_model, effort="high",
            marker="LAUNCH-FAKE",
        )
        if over_out:
            for p in Path(home).rglob("rollout-*.jsonl"):
                extra = {
                    "timestamp": "2023-11-14T22:13:20.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": over_out,
                                "cached_input_tokens": 0,
                                "output_tokens": over_out,
                                "reasoning_output_tokens": 0,
                                "total_tokens": over_out,
                            }
                        },
                    },
                }
                with p.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(extra) + "\n")
    elif exe == "grok":
        home = os.environ.get("GROK_HOME") or str(Path(os.environ.get("HOME", "")) / ".grok")
        stores.build_grok_store(
            Path(home), cwd, base_timestamp=epoch, session_id=stream_id,
            model=ran_model, marker="LAUNCH-FAKE",
            head_commit=head_commit or None,
            git_root_dir=cwd,
            grok_home=home,
        )

note = os.environ.get("TASK_LAUNCH_NOTE")
if note == "":
    note = None
wrapper = os.environ.get("TASK_LAUNCH_WRAPPER") or ""

def envelope_obj():
    env = {
        "job": job,
        "status": status,
        "verdict": None if verdict == "null" else verdict,
        "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
        "findings": [],
        "artifacts": {},
        "spend": {"harness": exe if sys.argv else "codex",
                  "total": 0, "out": 0, "runs": 1},
        "stamp": {"ref": "harness-placeholder", "started": None, "ended": None},
        "note": note,
    }
    if envelope_commit:
        env["commit"] = {"subject": "dispatch: pin U10", "body": "why"}
    return env

# U10 knobs: TASK_LAUNCH_WRAPPER=claude|codex|grok|plain emits each
# documented stdout/file shape so the E2E path is provable without a
# token. Default empty/plain keeps today's last-JSON-object scan.
if wrapper == "claude":
    field = os.environ.get("TASK_LAUNCH_WRAPPER_FIELD") or "structured_output"
    subtype = os.environ.get("TASK_LAUNCH_WRAPPER_SUBTYPE") or "success"
    is_error = os.environ.get("TASK_LAUNCH_WRAPPER_IS_ERROR") == "1"
    usage_mode = os.environ.get("TASK_LAUNCH_WRAPPER_USAGE") or "tokens"
    wrap = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "session_id": session_id or stream_id,
    }
    if usage_mode == "empty":
        wrap["usage"] = {}
    elif usage_mode == "zero":
        wrap["usage"] = {"input_tokens": 0, "output_tokens": 0}
        wrap["total_cost_usd"] = 0.0
    elif usage_mode == "cached":
        wrap["usage"] = {
            "input_tokens": 12,
            "output_tokens": 3000,
            "cache_read_input_tokens": 480000,
            "cache_creation_input_tokens": 9000,
        }
        wrap["total_cost_usd"] = 0.5
    else:
        wrap["usage"] = {"input_tokens": 10, "output_tokens": 4}
        wrap["total_cost_usd"] = 0.001
    env = envelope_obj()
    decoy_note = os.environ.get("TASK_LAUNCH_WRAPPER_DECOY")
    if decoy_note:
        # Nested sibling before structured_output: a walk that takes the
        # first nine-key dict is not reading the documented field.
        alt = envelope_obj()
        alt["note"] = decoy_note
        wrap["alt"] = alt
    if field == "structured_output":
        wrap["structured_output"] = env
        wrap["result"] = "ok"
    elif field == "result":
        wrap["result"] = json.dumps(env)
    else:
        wrap["result"] = os.environ.get("TASK_LAUNCH_WRAPPER_RESULT") or (
            "max turns reached"
        )
    sys.stdout.write(json.dumps(wrap) + "\n")
elif wrapper == "codex":
    env = envelope_obj()
    field = os.environ.get("TASK_LAUNCH_WRAPPER_FIELD") or "file"
    agent_note = os.environ.get("TASK_LAUNCH_WRAPPER_AGENT_NOTE") or ""
    out_path = last_message
    events = [
        {"type": "thread.started", "thread_id": session_id or stream_id},
        {"type": "turn.started"},
    ]
    if field in ("agent_message", "both"):
        agent_env = envelope_obj()
        if agent_note:
            agent_env["note"] = agent_note
        events.append({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(agent_env)},
        })
    else:
        events.append({
            "type": "item.completed",
            "item": {"type": "reasoning", "text": "working"},
        })
    for ev in events:
        sys.stdout.write(json.dumps(ev) + "\n")
    if field == "stdout":
        # Bare nine-key object after NDJSON with no agent_message and
        # no -o file — the pre-U10 stdout fallback D-ENV-1 keeps.
        sys.stdout.write(json.dumps(env) + "\n")
    elif field in ("file", "both"):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(env) + "\n", encoding="utf-8")
elif wrapper == "grok":
    env = envelope_obj()
    invalid = os.environ.get("TASK_LAUNCH_WRAPPER_INVALID") or ""
    if invalid == "extra":
        env["transcript"] = "the whole conversation"
    elif invalid == "missing":
        env.pop("note", None)
    elif invalid == "counts-empty":
        env["counts"] = {}
    elif invalid == "counts-p1-string":
        env["counts"]["p1"] = "lots"
    elif invalid == "findings-str":
        env["findings"] = ["not a finding"]
    elif invalid == "findings-int":
        env["findings"] = [42]
    elif invalid == "artifacts-int":
        env["artifacts"] = {"plan": 1}
    elif invalid == "artifacts-obj":
        env["artifacts"] = {"plan": {"inline": "..."}}
    elif invalid == "commit-null":
        env["commit"] = None
    elif invalid == "stamp-extra":
        env["stamp"]["extra"] = "x"
    elif invalid == "invalid-approve":
        env["status"] = "invalid"
        env["verdict"] = "approve"
    narration = os.environ.get("TASK_LAUNCH_WRAPPER_NARRATION") or ""
    if narration:
        sys.stdout.write(narration)
    decoy_first = os.environ.get("TASK_LAUNCH_WRAPPER_DECOY_FIRST")
    if decoy_first:
        # Fully-valid decoy BEFORE the harness object (D-ENV-2: two
        # fully-valid objects, the last wins; a first-valid reader
        # would take this one).
        first = envelope_obj()
        first["note"] = decoy_first
        first.pop("commit", None)
        sys.stdout.write(json.dumps(first) + "\n")
    sys.stdout.write(json.dumps(env) + "\n")
    # Trailing scan-shaped decoy AFTER the harness object (feature
    # scenario "a grok trailing nested-invalid object loses to an
    # earlier valid envelope"): nine top-level keys, nested
    # counts.p1 a string so last-that-validates must skip it.
    decoy_note = os.environ.get("TASK_LAUNCH_WRAPPER_DECOY")
    if decoy_note:
        decoy = envelope_obj()
        decoy["note"] = decoy_note
        decoy.pop("commit", None)
        decoy["verdict"] = "approve"
        decoy["status"] = "ok"
        decoy["counts"] = {"p1": "scan", "p2": 0, "p3": 0, "opinions": 0}
        sys.stdout.write(json.dumps(decoy) + "\n")
elif stdout_mode == "envelope":
    sys.stdout.write(json.dumps(envelope_obj()) + "\n")
elif stdout_mode == "token":
    sys.stdout.write(token + "\n")
elif stdout_mode == "prose":
    sys.stdout.write("VERDICT: approve\n")
decoy_note = os.environ.get("TASK_LAUNCH_WRAPPER_DECOY")
if decoy_note and wrapper != "grok":
    # A trailing nine-key object the last-JSON-object scan would take.
    # First-source unwrap must ignore it and use the harness field/file.
    # Grok writes its own decoy after the harness object (see above).
    decoy = envelope_obj()
    decoy["note"] = decoy_note
    sys.stdout.write(json.dumps(decoy) + "\n")
sys.stdout.flush()

if sleep_s > 0:
    time.sleep(sleep_s)

if done_path:
    Path(done_path).write_text("COMPLETED\n", encoding="utf-8")

raise SystemExit(exit_code)
"""


class PlantFailed(Exception):
    """A fault could not be proven to have landed intact."""


class FakeClock:
    """Injected monotonic/sleep seam. Tests assign these onto launch.py."""

    def __init__(self, start=0.0):
        self.now = float(start)
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        seconds = float(seconds)
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.0)


def plant_bytes(path, mutate, *, expect="edit", recognisable=None) -> bytes:
    """Rewrite ``path`` through ``mutate``, proving the fault landed intact."""
    path = Path(path)
    before = path.read_bytes()
    if not before:
        raise PlantFailed(f"fixture {path} was already empty before planting")
    after = mutate(before)
    if not isinstance(after, (bytes, bytearray)):
        raise PlantFailed(f"mutate() returned {type(after).__name__}, not bytes")
    after = bytes(after)
    if after == before:
        raise PlantFailed(f"plant changed nothing in {path}")
    if not after:
        raise PlantFailed(f"plant emptied {path}")
    if expect == "edit" and len(after) != len(before):
        raise PlantFailed(f"plant claimed an in-place edit of {path}")
    if expect == "shrink" and len(after) >= len(before):
        raise PlantFailed(f"plant claimed to shrink {path}")
    if expect == "grow" and len(after) <= len(before):
        raise PlantFailed(f"plant claimed to grow {path}")
    if expect not in ("edit", "shrink", "grow"):
        raise PlantFailed(f"unknown expect={expect!r}")
    if recognisable is not None and not recognisable(after):
        raise PlantFailed(f"plant left {path} unrecognisable")
    path.write_bytes(after)
    landed = path.read_bytes()
    if landed != after:
        raise PlantFailed(f"plant did not reach disk for {path}")
    return before


def _absent_launch():
    """Empty-stub stand-in: main returns 0 and writes nothing."""
    mod = types.ModuleType("task_launch_absent")

    def main(argv=None):
        return 0

    mod.main = main
    mod.JOBS_PATH = None
    mod.monotonic = time.monotonic
    mod.sleep = time.sleep
    return mod


def _absent_record():
    """Empty-stub stand-in: build echoes, validate is a no-op."""
    mod = types.ModuleType("task_record_absent")

    def build(payload=None, **kwargs):
        if payload is None:
            payload = kwargs
        if isinstance(payload, dict):
            return dict(payload)
        return {}

    def validate(rec):
        return rec

    mod.build = build
    mod.validate = validate
    mod.FIELDS = ()
    return mod


def require_module(test, name):
    """Load ``ops/devlane/task/<name>.py``, or an empty stub if it is absent.

    A missing file is not the red. The red is the test's contracted
    assertion against a module that does not yet implement the
    behaviour. An empty file on disk is wired the same way: missing
    ``main`` / ``build`` / ``validate`` become no-ops so the assertion
    the test is for is the one that fails.
    """
    path = APP / f"{name}.py"
    if not path.is_file():
        if name == "launch":
            return _absent_launch()
        if name == "record":
            return _absent_record()
        test.fail(f"{name}.py is not present at {path}")
    try:
        module = support.load(name)
    except Exception as exc:
        test.fail(
            f"{name}.py exists but did not load: "
            f"{type(exc).__name__}: {exc}"
        )
    if name == "launch" and not hasattr(module, "main"):
        module.main = lambda argv=None: 0
    if name == "record":
        if not hasattr(module, "build"):
            def _echo(payload=None, **kwargs):
                if payload is None:
                    payload = kwargs
                return dict(payload) if isinstance(payload, dict) else {}
            module.build = _echo
        if not hasattr(module, "validate"):
            module.validate = lambda rec: rec
        if not hasattr(module, "FIELDS"):
            module.FIELDS = ()
    return module


def load_path(test, path, name):
    path = Path(path)
    test.assertTrue(path.is_file(), f"required module is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    test.assertIsNotNone(spec, f"could not create an import spec for {path}")
    test.assertIsNotNone(spec.loader, f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "XDG_CONFIG_HOME"}
    env.update({
        "HOME": str(home),
        "GIT_AUTHOR_NAME": "launch-test",
        "GIT_AUTHOR_EMAIL": "launch-test@example.test",
        "GIT_COMMITTER_NAME": "launch-test",
        "GIT_COMMITTER_EMAIL": "launch-test@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def claude_slug(cwd: str) -> str:
    return cwd.replace("/", "-").replace(".", "-")


def residual_entries(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [ln for ln in value.splitlines() if ln.strip()]
    return list(value)


def changed_entries(value):
    return residual_entries(value)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pid_is_alive(pid: int) -> bool:
    # A group SIGKILL can leave a descendant as a zombie until the host's
    # init process reaps it.  ``kill(pid, 0)`` still succeeds for zombies,
    # even though they cannot execute and therefore are not survivors of the
    # isolation boundary.  Check procfs first so the process-group assertion
    # measures live workers rather than the reaping behaviour of PID 1 (which
    # is notably delayed in some CI containers).
    stat = Path(f"/proc/{pid}/stat")
    try:
        if stat.read_text(encoding="utf-8").split()[2] == "Z":
            return False
    except (FileNotFoundError, IndexError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_if_alive(pid) -> None:
    if not pid:
        return
    with contextlib.suppress(OSError, ProcessLookupError, ValueError):
        os.kill(int(pid), 9)


class _TempLaunch(unittest.TestCase):
    """Throwaway repo on branch ``work``, isolated HOME, fake CLIs."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.repo = self.home / "repo"
        self.bin = self.home / "bin"
        self.jobs_root = self.home / "jobs"
        self.repo.mkdir()
        self.bin.mkdir()
        self.jobs_root.mkdir()

        self._orig_env = {
            k: os.environ.get(k) for k in (
                "PATH", "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
                "WF_AGENT", "DISPATCH_JOBS", "DISPATCH_STREAM_GRACE",
                "DISPATCH_TIMEOUT", "TASK_LAUNCH_WITNESS", "TASK_LAUNCH_DONE",
                "TASK_LAUNCH_SLEEP", "TASK_LAUNCH_STDOUT",
                "TASK_LAUNCH_TOKEN", "TASK_LAUNCH_JOB",
                "TASK_LAUNCH_VERDICT", "TASK_LAUNCH_STATUS",
                "TASK_LAUNCH_ENVELOPE_COMMIT", "TASK_LAUNCH_RAN_MODEL",
                "TASK_LAUNCH_STREAM_EPOCH", "TASK_LAUNCH_STORES",
                "TASK_LAUNCH_WRITE_STREAM", "TASK_LAUNCH_IGNORE_SESSION",
                "TASK_LAUNCH_STREAM_ID", "TASK_LAUNCH_COMMIT",
                "TASK_LAUNCH_EDIT", "TASK_LAUNCH_ORPHAN",
                "TASK_LAUNCH_HEAD_COMMIT", "TASK_LAUNCH_EXIT",
                "TASK_LAUNCH_OVER_OUT", "TASK_LAUNCH_GRANDCHILD",
                "TASK_LAUNCH_WRAPPER", "TASK_LAUNCH_WRAPPER_FIELD",
                "TASK_LAUNCH_WRAPPER_SUBTYPE",
                "TASK_LAUNCH_WRAPPER_IS_ERROR",
                "TASK_LAUNCH_WRAPPER_INVALID",
                "TASK_LAUNCH_WRAPPER_NARRATION",
                "TASK_LAUNCH_WRAPPER_RESULT",
                "TASK_LAUNCH_WRAPPER_DECOY",
                "TASK_LAUNCH_WRAPPER_DECOY_FIRST",
                "TASK_LAUNCH_WRAPPER_USAGE",
                "TASK_LAUNCH_WRAPPER_AGENT_NOTE", "TASK_LAUNCH_NOTE",
                "CLICOLOR_FORCE", "FORCE_COLOR",
                "NO_COLOR", "CODEX_HOME", "GROK_HOME", "CLAUDE_CONFIG_DIR",
            )
        }
        self._saved_git = {k: os.environ[k] for k in list(os.environ)
                           if k.startswith("GIT_")}
        for k in list(self._saved_git):
            del os.environ[k]
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("CLICOLOR_FORCE", None)
        os.environ.pop("FORCE_COLOR", None)
        os.environ.pop("CODEX_HOME", None)
        os.environ.pop("GROK_HOME", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("TASK_LAUNCH_RAN_MODEL", None)
        os.environ.pop("TASK_LAUNCH_STORES", None)
        os.environ.pop("DISPATCH_STREAM_GRACE", None)
        os.environ.pop("DISPATCH_TIMEOUT", None)

        os.environ["HOME"] = str(self.home)
        os.environ["PATH"] = str(self.bin) + os.pathsep + os.environ.get(
            "PATH", ""
        )
        os.environ["WF_AGENT"] = AGENT
        os.environ["DISPATCH_JOBS"] = str(self.jobs_root)
        os.environ["TASK_LAUNCH_STREAM_EPOCH"] = str(STREAM_EPOCH)
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        os.environ["TASK_LAUNCH_STDOUT"] = "envelope"
        os.environ["TASK_LAUNCH_VERDICT"] = "approve"

        self.env = git_env(self.home)
        self._git("init", "-b", "work")
        self._git("config", "user.name", "launch-test")
        self._git("config", "user.email", "launch-test@example.test")
        self._git("config", "commit.gpgsign", "false")

        jobs = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        jobs["withheld-whole"] = {
            "adapter": "harness",
            "deliverable": "fixture for history-vs-withheld",
            "role": "read",
            "snapshot": "whole",
            "withheld": ["secret.py"],
            "prompt": "Read {ref}. Aim at: {scope}",
            "constraints": ["read only"],
        }
        jobs["fileset-job"] = {
            "adapter": "harness",
            "deliverable": "fixture for mode-unavailable",
            "role": "read",
            "snapshot": "fileset",
            "prompt": "Read {ref}. Aim at: {scope}",
            "constraints": ["read only"],
        }
        jobs["needs-into"] = {
            "adapter": "harness",
            "deliverable": "fixture for template into/base/diff",
            "role": "read",
            "snapshot": "whole",
            "prompt": (
                "into={into} base={base} diff={diff} ref={ref} "
                "Aim at: {scope}"
            ),
            "constraints": ["read only"],
        }
        jobs["needs-hole"] = {
            "adapter": "harness",
            "deliverable": "fixture for a missing template value",
            "role": "read",
            "snapshot": "whole",
            "prompt": "this names {not_a_slot} and {scope}",
            "constraints": ["read only"],
        }
        self.jobs_file = self.repo / ".dev" / "app" / "task" / "jobs.json"
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_file.write_text(
            json.dumps(jobs, indent=2) + "\n", encoding="utf-8"
        )
        self._write("alpha.py", "alpha v1\n")
        self._write("README.md", "fixture tree\n")
        self.root_sha = self._commit("root")
        self._write("alpha.py", "alpha v2\n")
        self.mid_sha = self._commit("mid")
        self._write("alpha.py", "alpha v3\n")
        self.ref = self._commit("tip")
        self.lineage = "work"

        self._git("checkout", "-b", "side", self.root_sha)
        self._write("side.py", "off the lineage\n")
        self.side_sha = self._commit("side")
        self._git("checkout", "work")

        self.start_witness = self.home / "started.json"
        self.witness = self.home / "witness.json"
        self.grandchild = self.home / "grandchild.pid"
        os.environ["TASK_LAUNCH_WITNESS"] = str(self.witness)
        os.environ.pop("TASK_LAUNCH_DONE", None)
        os.environ.pop("TASK_LAUNCH_SLEEP", None)
        os.environ.pop("TASK_LAUNCH_IGNORE_SESSION", None)
        os.environ.pop("TASK_LAUNCH_STREAM_ID", None)
        os.environ.pop("TASK_LAUNCH_COMMIT", None)
        os.environ.pop("TASK_LAUNCH_EDIT", None)
        os.environ.pop("TASK_LAUNCH_ORPHAN", None)
        os.environ.pop("TASK_LAUNCH_HEAD_COMMIT", None)
        os.environ.pop("TASK_LAUNCH_EXIT", None)
        os.environ.pop("TASK_LAUNCH_OVER_OUT", None)
        os.environ.pop("TASK_LAUNCH_GRANDCHILD", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_FIELD", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_SUBTYPE", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_IS_ERROR", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_INVALID", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_NARRATION", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_RESULT", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_DECOY", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_DECOY_FIRST", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_USAGE", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_AGENT_NOTE", None)
        os.environ.pop("TASK_LAUNCH_NOTE", None)
        os.environ.pop("TASK_LAUNCH_STATUS", None)
        os.environ.pop("TASK_LAUNCH_ENVELOPE_COMMIT", None)

        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "auth.json").write_text("{}\n", encoding="utf-8")
        (self.home / ".grok").mkdir()
        (self.home / ".grok" / "auth.json").write_text("{}\n", encoding="utf-8")

        self._install_cli("claude")
        self._install_cli("codex")
        self._install_cli("grok")

        # Loaded on first use so a missing launch.py is an empty stub
        # whose main is a no-op — the test method's assertion is the red.
        self.launch = None
        self._saved_jobs_path = None

    def load_launch(self):
        if self.launch is not None:
            return self.launch
        self.launch = require_module(self, "launch")
        if hasattr(self.launch, "JOBS_PATH"):
            self._saved_jobs_path = self.launch.JOBS_PATH
            self.launch.JOBS_PATH = self.jobs_file
        return self.launch

    def tearDown(self):
        if self.start_witness.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
                info = json.loads(
                    self.start_witness.read_text(encoding="utf-8")
                )
                kill_if_alive(info.get("pid"))
                kill_if_alive(info.get("pgid"))
        if self.grandchild.is_file():
            with contextlib.suppress(OSError, ValueError):
                kill_if_alive(
                    self.grandchild.read_text(encoding="utf-8").strip()
                )
        if self.launch is not None and self._saved_jobs_path is not None:
            self.launch.JOBS_PATH = self._saved_jobs_path
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in list(os.environ):
            if k.startswith("GIT_"):
                del os.environ[k]
        os.environ.update(self._saved_git)
        self._td.cleanup()

    def _git(self, *args, repo=None):
        r = subprocess.run(
            ["git", *args], cwd=repo or self.repo, env=self.env,
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"git {args} failed ({r.returncode}): {r.stderr}")
        return r

    def _write(self, rel, content):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _commit(self, msg):
        self._git("add", "-A")
        self._git("commit", "-m", msg)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _install_cli(self, name):
        dest = self.bin / name
        script = (
            _FAKE_CLI
            .replace("@@START_WITNESS@@", json.dumps(str(self.start_witness)))
            .replace("@@RAN_MODEL@@", json.dumps(RAN_MODEL))
            .replace("@@STORES_PATH@@", json.dumps(str(STORES_PATH)))
        )
        dest.write_text(script, encoding="utf-8")
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                   | stat.S_IXOTH)
        return dest

    def set_grace(self, seconds):
        """External seam: DISPATCH_STREAM_GRACE, default 120 seconds."""
        os.environ["DISPATCH_STREAM_GRACE"] = str(seconds)

    def attach_clock(self):
        """Injected clock/poller seam: launch.monotonic and launch.sleep."""
        launch = self.load_launch()
        clock = FakeClock()
        launch.monotonic = clock.monotonic
        launch.sleep = clock.sleep
        return clock

    def force_id(self, job_id):
        launch = self.load_launch()
        launch.mint_id = lambda *a, **k: job_id
        return job_id

    def run_main(self, argv, *, cwd=None):
        launch = self.load_launch()
        out, err = io.StringIO(), io.StringIO()
        cwd = cwd or self.repo
        old = os.getcwd()
        try:
            os.chdir(cwd)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = launch.main(list(argv))
                except SystemExit as exc:
                    code = int(exc.code) if exc.code is not None else 0
        finally:
            os.chdir(old)
        if code is None:
            code = 0
        return int(code), out.getvalue(), err.getvalue()

    def argv_for(
        self,
        job="plan",
        *,
        harness="grok",
        model=REQUESTED_MODEL,
        ref=None,
        lineage=None,
        stage=None,
        scope="pin the launcher",
        extra=(),
    ):
        argv = [job, "--harness", harness]
        if model is not None:
            argv.extend(["--model", model])
        argv.extend(["--ref", ref or self.ref])
        if lineage is not None:
            argv.extend(["--lineage", lineage])
        if stage is not None:
            argv.extend(["--stage", stage])
        if scope is not None:
            argv.extend(["--scope", scope])
        argv.extend(list(extra))
        return argv

    def dispatch(self, argv=None, **kwargs):
        if argv is None:
            argv = self.argv_for(**kwargs)
        return self.run_main(argv)

    def combined(self, out, err):
        return (out or "") + (err or "")

    def assert_refusal(self, code, out, err, *, ident, phrases):
        text = self.combined(out, err)
        self.assertEqual(
            code, REFUSAL_EXIT,
            f"{ident} refuses with exit 3, got {code}: {text!r}",
        )
        lower = text.lower()
        self.assertIn("expected", lower, f"{ident} names expected: {text!r}")
        self.assertIn("found", lower, f"{ident} names found: {text!r}")
        self.assertIn("satisfy", lower, f"{ident} names satisfy: {text!r}")
        for phrase in phrases:
            self.assertIn(
                phrase.lower(), lower,
                f"{ident} text must name {phrase!r}: {text!r}",
            )
        return text

    def assert_not_started(self):
        self.assertFalse(
            self.start_witness.is_file(),
            "refusal must not start a child; baked start-witness exists: "
            + (self.start_witness.read_text(encoding="utf-8")
               if self.start_witness.is_file() else ""),
        )

    def job_dirs(self):
        if not self.jobs_root.exists():
            return []
        return [p for p in self.jobs_root.iterdir() if p.is_dir()]

    def record_files(self):
        root = self.repo / ".dev" / "records" / "dispatches"
        if not root.exists():
            return []
        return sorted(root.glob("*.json"))

    def the_job_dir(self):
        dirs = self.job_dirs()
        self.assertEqual(
            len(dirs), 1,
            f"expected exactly one job dir, found {len(dirs)}: {dirs}",
        )
        return dirs[0]

    def the_record_path(self):
        files = self.record_files()
        self.assertEqual(
            len(files), 1,
            f"expected exactly one record file, found {len(files)}: {files}",
        )
        return files[0]

    def read_record(self, path=None):
        path = path or self.the_record_path()
        self.assertTrue(path.is_file(), f"record missing: {path}")
        body = path.read_text(encoding="utf-8")
        self.assertTrue(body.strip(), "record file is empty")
        data = json.loads(body)
        self.assertIsInstance(data, dict)
        return data

    def read_start_witness(self):
        self.assertTrue(
            self.start_witness.is_file(),
            "the harness CLI must have been launched (start-witness missing)",
        )
        data = json.loads(self.start_witness.read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("argv"), list)
        self.assertTrue(data["argv"], "recorded argv is empty")
        return data

    def read_witness(self):
        self.assertTrue(
            self.witness.is_file(),
            "the harness CLI must have been launched (witness missing)",
        )
        data = json.loads(self.witness.read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("argv"), list)
        self.assertTrue(data["argv"], "recorded argv is empty")
        return data

    def launch_ok(self, argv=None, **kwargs):
        code, out, err = self.dispatch(argv, **kwargs)
        text = self.combined(out, err)
        self.assertNotEqual(
            code, REFUSAL_EXIT,
            f"happy path must not refuse (exit 3): {text!r}",
        )
        self.assertEqual(code, 0, f"happy path exits 0, got {code}: {text!r}")
        rec = self.read_record()
        self.assertEqual(rec.get("status"), "closed")
        return rec, self.read_witness(), out, err

    def snapshot_of(self, rec=None):
        rec = rec or self.read_record()
        snap = rec.get("snapshot") if isinstance(rec.get("snapshot"), dict) else {}
        root = snap.get("root")
        self.assertIsInstance(root, str)
        self.assertTrue(root.strip(), "snapshot.root must name a directory")
        path = Path(root)
        self.assertTrue(path.is_dir(), f"snapshot.root is not a dir: {root!r}")
        return path

    def refs_map(self, repo=None):
        raw = self._git(
            "for-each-ref", "--format=%(refname) %(objectname)",
            repo=repo,
        ).stdout.splitlines()
        out = {}
        for line in raw:
            if not line.strip():
                continue
            name, sha = line.split(" ", 1)
            out[name] = sha
        self.assertTrue(out, "fixture must have at least one ref")
        return out

    def fetch_head_bytes(self):
        p = self.repo / ".git" / "FETCH_HEAD"
        if not p.exists():
            return None
        return p.read_bytes()

    def porcelain(self, repo=None):
        return self._git(
            "status", "--porcelain=v1", "-uall", repo=repo,
        ).stdout

    def index_blob(self, repo=None):
        return self._git("ls-files", "-s", repo=repo).stdout

    def worktree_bytes(self, repo=None):
        """Index + porcelain + untracked file bytes, for exact delta."""
        root = Path(repo or self.repo)
        return {
            "porcelain": self.porcelain(repo=root),
            "index": self.index_blob(repo=root),
        }

    def worktree_paths(self, repo=None):
        raw = self._git("worktree", "list", "--porcelain", repo=repo).stdout
        paths = []
        for line in raw.splitlines():
            if line.startswith("worktree "):
                paths.append(line.split(" ", 1)[1])
        self.assertTrue(paths, "worktree list must name the invoking repo")
        return paths

    def git_dir_mentions(self, snapshot: Path, needle: bytes) -> list:
        git = snapshot / ".git"
        hits = []
        if not git.exists():
            return hits
        for p in git.rglob("*"):
            if p.is_symlink():
                try:
                    target = os.fsencode(os.readlink(p))
                except OSError:
                    continue
                if needle in target:
                    hits.append(str(p.relative_to(git)) + " (symlink)")
                continue
            if not p.is_file():
                continue
            try:
                body = p.read_bytes()
            except OSError:
                continue
            if needle in body:
                hits.append(str(p.relative_to(git)))
        return hits

    def assert_objects_not_shared(self, snapshot: Path):
        snap_obj = snapshot / ".git" / "objects"
        src_obj = self.repo / ".git" / "objects"
        self.assertTrue(snap_obj.exists(), "snapshot has an object store")
        self.assertFalse(
            snap_obj.is_symlink(),
            ".git/objects must not be a symlink to the invoking store",
        )
        git = snapshot / ".git"
        self.assertFalse(
            git.is_file(),
            "snapshot .git is a file (a worktree pointer), not a repo",
        )
        if snap_obj.is_dir() and src_obj.is_dir():
            self.assertFalse(
                os.path.samefile(snap_obj, src_obj),
                "snapshot objects dir is the invoking objects dir",
            )
        src_inodes = set()
        for p in src_obj.rglob("*"):
            if p.is_symlink() or not p.is_file():
                continue
            st = p.stat()
            src_inodes.add((st.st_dev, st.st_ino))
        for p in snap_obj.rglob("*"):
            self.assertFalse(
                p.is_symlink(),
                f"snapshot object is a symlink: {p}",
            )
            if not p.is_file():
                continue
            st = p.stat()
            self.assertNotIn(
                (st.st_dev, st.st_ino), src_inodes,
                f"hardlinked object {p} shares inode with the invoking store",
            )

    def dispatch_reflog(self, job_id):
        r = subprocess.run(
            ["git", "reflog", "show", f"refs/dispatch/{job_id}"],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
        )
        return r.stdout

    def dead_pid(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pid = proc.pid
        proc.kill()
        proc.wait()
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        return pid

    def plant_new_file(self, path, content, *, must_contain=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.assertTrue(data, "planted file must not be empty")
        path.write_bytes(data)
        landed = path.read_bytes()
        self.assertEqual(landed, data, "plant did not reach disk")
        self.assertGreater(len(landed), 0)
        if must_contain is not None:
            needle = (must_contain.encode("utf-8")
                      if isinstance(must_contain, str) else must_contain)
            self.assertIn(needle, landed, "plant missing its marker")
        return landed
