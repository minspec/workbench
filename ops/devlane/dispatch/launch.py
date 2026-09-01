#!/usr/bin/env python3
"""Policy launcher for dev-lane task dispatches."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import string
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import record

APP = Path(__file__).resolve().parent
# jobs.json stays in the task app (it is data, read by path, not an import);
# dispatch composes task's job registry with harness's isolation/wires.
JOBS_PATH = APP.parent / "task" / "jobs.json"
HARNESS_APP = APP.parent / "harness"
TELEMETRY_APP = APP.parent / "telemetry"
REFUSAL = 3
STAGES = ("plan", "tests", "check-tests", "code", "review", "adjudicate")
OWNER_NAME = "xormania"
OWNER_EMAIL = "127287135+xormania@users.noreply.github.com"
monotonic = time.monotonic
sleep = time.sleep


class Refused(Exception):
    pass


def _module(name, path):
    spec = importlib.util.spec_from_file_location(f"task_launch_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENVELOPE = _module("envelope", APP.parent / "task" / "envelope.py")
ENVELOPE_SCHEMA_JSON = json.dumps(ENVELOPE.ENVELOPE_SCHEMA, separators=(",", ":"))


def _git(repo, *args, check=True, env=None, input=None):
    e = dict(os.environ if env is None else env)
    e.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
              "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=e,
        input=input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and p.returncode:
        raise RuntimeError(p.stderr.strip() or f"git exited {p.returncode}")
    return p


def _refuse(ident, expected, found, satisfy):
    raise Refused(f"{ident}: expected {expected}; found {found}; satisfy by {satisfy}")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mint_id(stage, harness):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{stage}-{harness}-{os.urandom(3).hex()}"


def _jobs_root():
    if os.environ.get("DISPATCH_JOBS"):
        return Path(os.environ["DISPATCH_JOBS"])
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path(os.environ.get("HOME", "")) / ".local/state"
    return base / "minspec" / "dispatch"


def _repo():
    p = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(p.stdout.strip()).resolve()


def _branch(repo):
    p = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return p.stdout.strip() if p.returncode == 0 else "detached"


def _identity():
    value = os.environ.get("WF_AGENT")
    if not value or not re.fullmatch(r"[^<>\n]+ <[^<>\s@]+@[^<>\s]+>", value):
        _refuse("identity", "an identity in the Name <address> form",
                value or "unset", "export WF_AGENT='Your Model Name <noreply@vendor>'")
    return value


def _inside(path, root):
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _parse_override(items, agent):
    out = []
    for item in items:
        ident, sep, reason = item.partition(":")
        if ident != "stale-base" or not sep or not reason.strip():
            _refuse("override", "stale-base with a non-empty reason", item,
                    "drop the override or pass stale-base:REASON")
        out.append({"refusal": ident, "reason": reason, "by": agent})
    return out


def _snapshot(repo, lineage, sha, root):
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "core.logAllRefUpdates", "false")
    _git(root, "fetch", "--quiet", str(repo),
         f"refs/heads/{lineage}:refs/heads/{lineage}")
    # An explicitly overridden off-lineage commit need not be reachable
    # from the lineage ref fetched above.
    _git(root, "fetch", "--quiet", str(repo), sha)
    (root / ".git" / "FETCH_HEAD").unlink(missing_ok=True)
    shutil.rmtree(root / ".git" / "logs", ignore_errors=True)
    _git(root, "checkout", "--quiet", "--detach", sha)
    shutil.rmtree(root / ".git" / "logs", ignore_errors=True)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _render(template, values):
    fields = [name for _, name, _, _ in string.Formatter().parse(template) if name]
    missing = [name for name in fields if name not in values]
    if missing:
        _refuse("render", "every template value supplied", missing[0],
                f"supply {missing[0]} before rendering")
    return template.format(**values)


def _harness_meta(name, role, job_dir, env):
    isolation = _module("isolation", HARNESS_APP / "isolation.py")
    try:
        iso_env, flags = isolation.isolated(name, job_dir / "home" / name, env)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        _refuse("isolation", "an isolated harness with credentials", str(exc),
                "add the isolation entry and required credential")
    spec = isolation.HARNESSES[name]
    # Read roles still write: plan, check-tests and adjudicate deliver a
    # file under the job's out/, and check-tests runs a suite that needs a
    # writable tempdir. codex read-only denied both (2026-08-28: "sandbox
    # rejected writes as read-only ... no affected assertion ran"), and
    # plan mode on claude and grok ends the turn at a plan. The snapshot's
    # integrity is proved after the run instead: a read role's HEAD must
    # equal ref_sha and residual_paths is recorded.
    sandbox = {"claude": "acceptEdits", "codex": "workspace-write", "grok": "always-approve"}[name]
    containment = "os" if name == "codex" else "policy"
    home = iso_env.get(spec.get("home_env", ""))
    store_base = Path(home) if spec["sessions"]["under"] == "minimal" else Path(env["HOME"]) / ".claude"
    store = store_base / spec["sessions"]["path"]
    data = {"mechanism": spec["mechanism"],
            "flags" if spec["mechanism"] == "flags" else "env": flags if spec["mechanism"] == "flags" else iso_env,
            "store": str(store), "observed": {"unresolved": "behavioural probe has not run"}}
    if home:
        data.update(home=home, auth_files=list(spec["auth_files"]))
    return iso_env, flags, sandbox, containment, data


def _preflight_isolation(name, target, env):
    isolation = _module("isolation_preflight", HARNESS_APP / "isolation.py")
    spec = isolation.HARNESSES.get(name)
    if spec is None:
        _refuse("isolation", "a harness isolation entry", name,
                "add an isolation entry with a measurement")
    if target.exists() and any(target.iterdir()):
        found = ", ".join(p.name for p in target.iterdir())
        _refuse("isolation", "an empty minimal home", found,
                "use a fresh job id and empty home")
    if spec["mechanism"] == "home":
        named = spec.get("home_env")
        source = Path(env.get(named) or Path(env.get("HOME", "")) / f".{name}")
        missing = [f for f in spec["auth_files"] if not (source / f).exists()]
        if missing:
            _refuse("isolation", "the required credential", str(source / missing[0]),
                    "install the credential in the harness home")


# A read role must be able to execute to judge: a skeptic that cannot run the
# suite or plant a mutant is structural (2026-08-29: four check-tests/review
# dispatches on claude reported "This command requires approval" for
# `python3 -c 'print(2+2)'` under --print acceptEdits). These rules are
# what the claude child may run without a prompt. Honest limits, measured
# by the first review round (ba0d93, ffaf14): claude has no home isolation
# (CONTRACT.md §isolation: mechanism `flags`), so anything a rule allows
# runs with the operator's uid. The list therefore names verification
# commands, never an interpreter (`python3 *`, `env *`, `find *`, `sed *`
# were arbitrary code and are gone); `python3 .dev/*` runs the snapshot's
# own scripts, the same trust as running its suite. A write into the
# snapshot is recorded as residual and preserved (residual.patch), not
# refused. Real containment is Claude Code's OS sandbox — plan U2, gate
# G-CLI-1 — not this list.
CLAUDE_TOOL_RULES = (
    "Bash(python3 -m unittest *)", "Bash(python3 -m pytest *)",
    "Bash(python3 -m ruff *)", "Bash(python3 .dev/*)",
    "Bash(ruff check *)", "Bash(ruff format --check *)",
    "Bash(cue vet *)", "Bash(cue export *)", "Bash(cue eval *)", "Bash(cue version)",
    "Bash(git diff *)", "Bash(git log *)", "Bash(git show *)",
    "Bash(git status *)", "Bash(git rev-parse *)", "Bash(git ls-files *)",
    "Bash(git grep *)", "Bash(git blame *)",
    "Bash(ls *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
    "Bash(grep *)", "Bash(rg *)", "Bash(wc *)", "Bash(uniq *)", "Bash(diff *)",
    "Bash(sha256sum *)", "Bash(jq *)", "Bash(which *)", "Bash(test *)", "Bash(cd *)",
)
# Tools no dispatch may use: the network (jobs say "no network") and
# sub-agents (the owner's hard rule, 2026-08-29).
CLAUDE_DENIED_TOOLS = ("WebFetch", "WebSearch", "Agent", "Task")


def _grok_permission(sandbox):
    """`--always-approve` is grok's own flag, not a --permission-mode value;
    web search and fetch are switched off beside it because every job says
    "no network" and always-approve would otherwise approve them too."""
    if sandbox == "always-approve":
        return ["--always-approve", "--disable-web-search"]
    return ["--permission-mode", sandbox, "--disable-web-search"]


def _argv(name, model, effort, session, prompt, flags, sandbox, resume=False):
    executable = shutil.which(name) or name
    last_message = str((Path(prompt).parent / "out" / "last-message.json").resolve())
    if resume:
        if name == "codex":
            return [executable, "exec", "resume", session, "--model", model,
                    "--json", "-o", last_message]
        if name == "claude":
            out = str((Path(prompt).parent / "out").resolve())
            incoming = str((Path(prompt).parent / "in").resolve())
            context = str((Path(prompt).parent / "context").resolve())
            return [executable, *flags, "-r", session, "--print",
                    "--permission-mode", sandbox, "--add-dir", out,
                    "--add-dir", incoming, "--add-dir", context,
                    "--allowedTools", *CLAUDE_TOOL_RULES,
                    "--disallowedTools", *CLAUDE_DENIED_TOOLS,
                    "--model", model, "--output-format", "json",
                    "--json-schema", ENVELOPE_SCHEMA_JSON]
        # grok resume must carry the same flags as a fresh launch, or B1
        # returns on every resumed dispatch (review ffaf14).
        argv = [executable, *flags, "-r", session, "--model", model,
                "--output-format", "plain"]
        argv += _grok_permission(sandbox)
        if effort:
            argv += ["--reasoning-effort", effort]
        argv += ["--prompt-file", str(prompt)]
        return argv
    out = str((Path(prompt).parent / "out").resolve())
    if name == "claude":
        incoming = str((Path(prompt).parent / "in").resolve())
        context = str((Path(prompt).parent / "context").resolve())
        return [executable, *flags, "--session-id", session, "--print",
                "--permission-mode", sandbox, "--add-dir", out,
                "--add-dir", incoming, "--add-dir", context,
                "--allowedTools", *CLAUDE_TOOL_RULES,
                "--disallowedTools", *CLAUDE_DENIED_TOOLS,
                "--model", model, "--output-format", "json",
                "--json-schema", ENVELOPE_SCHEMA_JSON]
    if name == "codex":
        # codex exec takes the brief on stdin; "-" says so explicitly. With
        # DEVNULL it exits 1 "No prompt provided via stdin" before any work.
        # out/ sits outside the snapshot, so the workspace sandbox is told
        # it is writable; nothing else outside cwd and /tmp is.
        return [executable, "exec", "--sandbox", sandbox,
                "-c", f'sandbox_workspace_write.writable_roots=["{out}"]',
                "--model", model, "--json", "-o", last_message, "-"]
    # grok: the record states `sandbox`; the argv must carry it, or the
    # record claims a permission mode the child never had. plain output
    # keeps stdout parseable as the envelope. `always-approve` is grok's
    # own flag, not a --permission-mode value: under `auto` a session
    # raised 111 permission prompts and the last one timed out after
    # 30 s on a non-interactive stdin, ending the turn with no envelope
    # (2026-08-29, record 20260829T170852Z-tests-grok-abfc3b).
    # grok 1.0.5 with `--json-schema` answers after ONE model call with a
    # schema-valid but empty envelope and ends the turn (measured
    # 2026-08-29, record 20260829T234838Z-review-grok-66ccb2: 213 output
    # tokens, "note": "starting: reading briefs and review contract",
    # zero findings, $0.0067) — the structured-output mode short-circuits
    # the agentic loop. Until the probe matrix finds a grok mode that both
    # works and yields the object, grok keeps plain output and the scan.
    argv = [executable, *flags, "-s", session, "--model", model,
            "--output-format", "plain"]
    argv += _grok_permission(sandbox)
    if effort:
        argv += ["--reasoning-effort", effort]
    argv += ["--prompt-file", str(prompt)]
    return argv


def _child_env(base, additions, job_id):
    env = dict(base)
    for key in ("CLICOLOR_FORCE", "FORCE_COLOR", "CLAUDE_CONFIG_DIR"):
        env.pop(key, None)
    env.update({"NO_COLOR": "1", "CLICOLOR": "0", "TERM": "dumb",
                "PAGER": "cat", "GH_PAGER": "cat", "GIT_PAGER": "cat",
                "LESS": "FRX", "CI": "true", "GIT_TERMINAL_PROMPT": "0",
                "GIT_EDITOR": "true", "EDITOR": "true", "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8", "LC_ALL": "C.UTF-8",
                "WF_LANE": "dev", "DISPATCH_JOB": job_id})
    env.update(additions)
    return env


def _stream(job_dir, harness, session, snapshot, ref_sha):
    if harness == "codex":
        paths = list((job_dir / "home/codex/sessions").rglob("rollout-*.jsonl"))
    elif harness == "grok":
        paths = list((job_dir / "home/grok/sessions").rglob("summary.json"))
    else:
        slug = str(snapshot).replace("/", "-").replace(".", "-")
        paths = list((Path(os.environ["HOME"]) / ".claude/projects" / slug).glob("*.jsonl"))
    if not paths:
        return None, None, None, None
    path = max(paths, key=lambda p: p.stat().st_mtime)
    ran = None
    stream_id = None
    mismatch = None
    try:
        if path.name == "summary.json":
            data = json.loads(path.read_text())
            ran = data.get("current_model_id")
            stream_id = (data.get("info") or {}).get("id")
            if data.get("head_commit") and data["head_commit"] != ref_sha:
                mismatch = "head_commit mismatch"
        else:
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row.get("type") == "assistant":
                    ran = (row.get("message") or {}).get("model") or ran
                    stream_id = row.get("sessionId") or stream_id
                payload = row.get("payload") or {}
                if row.get("type") == "session_meta":
                    stream_id = payload.get("id")
                if row.get("type") == "turn_context":
                    ran = payload.get("model")
    except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        mismatch = f"stream parse: {exc}"
    if stream_id and harness != "codex" and stream_id != session:
        mismatch = f"session mismatch: minted {session}, found {stream_id}"
    return path, ran, stream_id, mismatch


ENVELOPE_KEYS = ("job", "status", "verdict", "counts", "findings",
                 "artifacts", "spend", "stamp", "note")


def _invalid(job, ref_sha, note):
    return {"job": job, "status": "invalid", "verdict": None, "counts": {},
            "findings": [], "artifacts": {}, "spend": {},
            "stamp": {"ref": ref_sha}, "note": note}


def _invalidate_envelope(data, note):
    data["status"] = "invalid"
    data["verdict"] = None
    prior = data.get("note")
    data["note"] = f"{prior}; {note}" if prior else note
    return data


def _json_objects(text):
    decoder = json.JSONDecoder()
    found = []
    for match in re.finditer(r"\{", text):
        try:
            candidate, _end = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            found.append(candidate)
    return found


def _schema_valid(data):
    def matches(value, schema):
        kinds = schema.get("type")
        kinds = [kinds] if isinstance(kinds, str) else kinds
        checks = {"object": lambda: isinstance(value, dict),
                  "array": lambda: isinstance(value, list),
                  "string": lambda: isinstance(value, str),
                  "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
                  "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
                  "boolean": lambda: isinstance(value, bool),
                  "null": lambda: value is None}
        if kinds and not any(checks[kind]() for kind in kinds):
            return False
        if "enum" in schema and value not in schema["enum"]:
            return False
        if isinstance(value, (int, float)) and "minimum" in schema and value < schema["minimum"]:
            return False
        if isinstance(value, list) and "items" in schema:
            return all(matches(item, schema["items"]) for item in value)
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            if any(key not in value for key in schema.get("required", [])):
                return False
            extra = set(value) - set(properties)
            additional = schema.get("additionalProperties", True)
            if extra and additional is False:
                return False
            if any(not matches(value[key], properties[key])
                   for key in set(value) & set(properties)):
                return False
            if isinstance(additional, dict) and any(
                    not matches(value[key], additional) for key in extra):
                return False
        return True

    return (matches(data, ENVELOPE.ENVELOPE_SCHEMA)
            and not (data.get("status") == "invalid"
                     and data.get("verdict") == "approve"))


def _most_envelope_shaped(objects, *, last=False):
    if not objects:
        return None
    score = max(sum(key in item for key in ENVELOPE_KEYS) for item in objects)
    matches = [item for item in objects
               if sum(key in item for key in ENVELOPE_KEYS) == score]
    return matches[-1] if last else matches[0]


def _wrapper_spend(wrapper):
    usage = wrapper.get("usage")
    if not isinstance(usage, dict):
        return None
    cached = (usage.get("cache_read_input_tokens") or 0) + (
        usage.get("cache_creation_input_tokens") or 0)
    spend = {
        "input": usage.get("input_tokens"),
        "cached": cached or None,
        "output": usage.get("output_tokens"),
        "source": "result.usage",
    }
    if wrapper.get("total_cost_usd") is not None:
        spend["cost_usd"] = wrapper["total_cost_usd"]
    spend = {key: value for key, value in spend.items() if value is not None}
    if not any(value for key, value in spend.items() if key != "source"):
        return None
    spend["total"] = sum(spend.get(key, 0) for key in ("input", "cached", "output"))
    return spend


def _merge_spend(prior, wrapper):
    if not wrapper:
        return prior
    merged = dict(prior) if isinstance(prior, dict) and "unresolved" not in prior else {}
    merged.update(wrapper)
    merged["total"] = sum(merged.get(key, 0) for key in ("input", "cached", "output"))
    return merged


def _envelope(raw, job, ref_sha, note=None, *, harness=None, job_dir=None,
              rec=None):
    """The last JSON object on stdout, or an invalid envelope that says why.

    Narration before or after the object is tolerated (a harness talks
    while it works); a missing key is a refusal, not a default; a `stamp`
    that is not an object is replaced rather than mislabeled as a model id
    (codex once returned the SHA as a string).
    """
    text = raw.decode("utf-8", "replace")
    objects = _json_objects(text)
    data = None
    cause = None
    if harness == "claude" and objects:
        wrapper = next((item for item in objects if item.get("type") == "result"), None)
        if wrapper is not None:
            if rec is not None:
                spend = _wrapper_spend(wrapper)
                if spend:
                    rec["session"]["spend"] = _merge_spend(
                        rec["session"].get("spend"), spend)
            if wrapper.get("is_error"):
                cause = f"claude-result:{wrapper.get('subtype') or 'unknown'}"
            elif isinstance(wrapper.get("structured_output"), dict):
                data = wrapper["structured_output"]
            elif isinstance(wrapper.get("result"), str):
                data = _most_envelope_shaped(_json_objects(wrapper["result"]),
                                             last=True)
            if data is None and cause is None:
                cause = f"claude-result:{wrapper.get('subtype') or 'unknown'}"
    elif harness == "codex":
        last = Path(job_dir) / "out" / "last-message.json" if job_dir else None
        if last is not None and last.is_file():
            try:
                candidate = json.loads(last.read_text(encoding="utf-8"))
                data = candidate if isinstance(candidate, dict) else None
            except (OSError, json.JSONDecodeError):
                data = None
        if data is None:
            for item in objects:
                payload = item.get("item")
                if isinstance(payload, dict) and payload.get("type") == "agent_message":
                    try:
                        candidate = json.loads(payload.get("text", ""))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        data = candidate
        if data is None:
            data = next((item for item in reversed(objects)
                         if _schema_valid(item)), None)
        if data is None:
            cause = "no-last-message"
    elif harness == "grok" and objects:
        # JSON output is the final top-level object; an earlier object can be
        # narration or a diagnostic and is not the requested result.
        data = next((item for item in reversed(objects)
                     if _schema_valid(item)), None)
        if data is None:
            data = _most_envelope_shaped(objects, last=True)
    if data is None and cause is None:
        # Compatibility for pre-U10 raw.out: choose the most envelope-shaped
        # complete object, preserving the first on ties.
        data = _most_envelope_shaped(objects)
    if data is None:
        if cause is None:
            cause = "envelope-parse"
        refusal = (f"{cause}: no structured envelope"
                   if cause != "envelope-parse"
                   else "envelope-parse: no JSON object on stdout")
        data = _invalid(job, ref_sha, refusal)
    elif harness is not None and not _schema_valid(data):
        cause = "schema-invalid"
        data = dict(data)
        data.update(status="invalid", verdict=None,
                    note="schema-invalid: object does not match ENVELOPE_SCHEMA")
    elif harness is None:
        missing = [key for key in ENVELOPE_KEYS if key not in data]
        if missing:
            data["status"] = "invalid"
            prior = data.get("note")
            refusal = f"envelope-missing: {', '.join(missing)}"
            data["note"] = f"{prior}; {refusal}" if prior else refusal
    stamp = data.get("stamp")
    if not isinstance(stamp, dict):
        data["stamp"] = {}
    data["stamp"]["ref"] = ref_sha
    if note:
        _invalidate_envelope(data, note)
    if rec is not None and cause:
        rec["_envelope_cause"] = cause
    return data


def _write_record(repo, rec):
    path = repo / ".dev/records/dispatches" / f"{rec['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.validate(rec), indent=2) + "\n", encoding="utf-8")
    return path


def _commit_record(repo, path, rec):
    rel = path.relative_to(repo)
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME=OWNER_NAME, GIT_AUTHOR_EMAIL=OWNER_EMAIL,
               GIT_COMMITTER_NAME=OWNER_NAME, GIT_COMMITTER_EMAIL=OWNER_EMAIL)
    msg = (f"dispatch: record {rec['id']}\n\nSource: generated: ops/devlane/dispatch/launch.py\n"
           f"Co-Authored-By: {rec['dispatched_by']}")
    for item in rec.get("overrides", []):
        msg += f"\n\nOverride: {item['refusal']}: {item['reason']}"
    _git(repo, "add", "--intent-to-add", "--", str(rel), env=env)
    _git(repo, "commit", "--only", "-m", msg, "--", str(rel), env=env)


def _run_attempt(rec, job_dir, *, job_caps=None, resume=False, reason=None):
    harness = rec["harness"]["name"]
    snapshot = Path(rec["snapshot"]["root"])
    prompt = job_dir / "prompt.txt"
    iso_env = rec["harness"]["isolation"].get("env", {})
    flags = rec["harness"]["isolation"].get("flags", [])
    argv = _argv(harness, rec["model"]["requested"], rec["model"]["effort_requested"],
                 rec["session"]["id"], prompt, flags, rec["harness"]["sandbox"], resume)
    rec["harness"]["argv"] = argv
    if harness == "codex":
        (job_dir / "out" / "last-message.json").unlink(missing_ok=True)
    env = _child_env(os.environ, iso_env, rec["id"])
    for marker in ("exit", "TRIPPED.md"):
        (job_dir / marker).unlink(missing_ok=True)
    launched = _now()
    raw_mode = "ab" if resume else "wb"
    # codex exec and claude --print read the brief from stdin ("Input must
    # be provided either through stdin or as a prompt argument"); grok names
    # the prompt file on argv and gets DEVNULL so nothing waits on a tty.
    feed = prompt.open("rb") if harness in ("codex", "claude") else None
    with (job_dir / "raw.out").open(raw_mode) as raw, (job_dir / "stderr").open(raw_mode) as err:
        proc = subprocess.Popen(argv, cwd=snapshot, env=env,
                                stdin=feed if feed is not None else subprocess.DEVNULL,
                                stdout=raw, stderr=err, start_new_session=True)
        if feed is not None:
            feed.close()
        state = {"pid": proc.pid, "pgid": proc.pid, "session": {"id": rec["session"]["id"]},
                 "stream": rec["session"].get("stream"), "attempt": len(rec["attempts"]) + 1}
        (job_dir / "state.json").write_text(json.dumps(state) + "\n")
        job_timeout = (job_caps or {}).get("timeout")
        timeout = float(os.environ.get(
            "DISPATCH_TIMEOUT", job_timeout if job_timeout is not None else "900"))
        rec["caps"]["timeout"] = timeout
        rec["caps"]["timeout_source"] = (
            "DISPATCH_TIMEOUT" if "DISPATCH_TIMEOUT" in os.environ
            else "job" if job_timeout is not None else "default")
        grace = float(os.environ.get("DISPATCH_STREAM_GRACE", "120"))
        started_clock = monotonic()
        runtime_note = None
        while proc.poll() is None:
            elapsed = monotonic() - started_clock
            stream, _ran, _sid, _mis = _stream(
                job_dir, harness, rec["session"]["id"], snapshot,
                rec["snapshot"]["ref_sha"],
            )
            over = False
            if stream and rec["caps"].get("cap-out") is not None:
                with contextlib.suppress(OSError):
                    over = any(int(n) > int(rec["caps"]["cap-out"])
                               for n in re.findall(r'"output_tokens"\s*:\s*(\d+)', stream.read_text()))
            if over:
                runtime_note = "trip: output cap exceeded"
                break
            if elapsed >= timeout:
                runtime_note = "timeout: harness exceeded runtime"
                break
            if not stream and elapsed >= grace:
                runtime_note = "unsupervised: no session stream within grace"
                break
            sleep(min(0.05, max(0.0, min(timeout, grace) - elapsed)))
        if runtime_note:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = 137
            (job_dir / "TRIPPED.md").write_text(runtime_note + "\n")
            rec["_runtime_note"] = runtime_note
        else:
            code = proc.returncode
    (job_dir / "exit").write_text(f"{code}\n")
    rec["attempts"].append({"n": len(rec["attempts"]) + 1, "launched": launched,
                            "ended": _now(), "exit": code,
                            "tripped": (job_dir / "TRIPPED.md").exists()})
    if reason:
        rec.setdefault("note", reason)
    return code


def _launch(args):
    agent = _identity()
    repo = _repo()
    branch = _branch(repo)
    lineage = args.lineage or branch
    if branch in {"detached", "dev", "main"} or branch != lineage:
        _refuse("record-target", f"the checkout on {lineage}", branch,
                f"run from a worktree of {lineage}")
    root = _jobs_root().resolve()
    wt = _git(repo, "worktree", "list", "--porcelain").stdout
    for line in wt.splitlines():
        if line.startswith("worktree ") and _inside(root, Path(line[9:])):
            _refuse("live-target", f"a job directory outside every worktree of {repo}",
                    f"{root} inside {line[9:]}", "set DISPATCH_JOBS outside the repository")
    jobs = json.loads(Path(JOBS_PATH).read_text())
    if args.job not in jobs or jobs[args.job].get("adapter") != "harness":
        _refuse("job", "a harness job in jobs.json", args.job, "name a dispatchable job")
    job = jobs[args.job]
    role = job.get("role")
    stage = args.stage or ("code" if role == "write" else "review")
    if not args.model:
        _refuse("model", "a model", "none", "pass --model")
    if args.harness == "codex" and args.effort:
        _refuse("model", "an effort-capable adapter", "effort on codex", "drop --effort")
    if args.harness == "claude" and role == "write":
        _refuse("write-role-unadmitted", "a write row with a containment entry",
                "none for claude", "run the containment probe or use codex or grok")
    mode = job.get("snapshot")
    if job.get("withheld") and mode == "whole":
        _refuse("history-vs-withheld", "one of history or withholding", f"both on {args.job}", "declare fileset")
    if mode != "whole":
        _refuse("mode-unavailable", "whole", f"{mode} on {args.job}", "stage it by hand")
    scope_bytes = len((args.scope or "").encode())
    takes_scope = "{scope}" in (job.get("prompt") or "")
    if scope_bytes > 1024 or (args.scope is not None and not takes_scope):
        _refuse("scope-cap", "<= 1024 bytes on a job that takes a scope",
                f"{scope_bytes} bytes on {args.job}", "shorten it or drop it")
    resolved = _git(repo, "rev-parse", "--verify", f"{args.ref}^{{commit}}", check=False)
    if resolved.returncode:
        _refuse("ref", f"a ref naming a commit in {repo}", f"{args.ref} ({resolved.stderr.strip()})",
                "commit the work, then name the commit")
    ref_sha = resolved.stdout.strip()
    overrides = _parse_override(args.override, agent)
    ancestor = _git(repo, "merge-base", "--is-ancestor", ref_sha, lineage, check=False).returncode == 0
    if not ancestor and not overrides:
        tip = _git(repo, "rev-parse", lineage).stdout.strip()
        _refuse("stale-base", f"{ref_sha[:8]} reachable from {lineage}",
                f"it is not ({lineage} is at {tip[:8]})", "name a commit on the branch or override stale-base:REASON")
    # The base a review diffs against is where the lineage left dev, not
    # the lineage's own tip -- merge-base(ref, lineage) is ref itself when
    # ref is the tip, and a review of an empty comparison reviews nothing.
    candidates = []
    for anchor in ("origin/dev", "dev"):
        probe = _git(repo, "merge-base", ref_sha, anchor, check=False)
        if probe.returncode == 0 and probe.stdout.strip():
            candidate = probe.stdout.strip()
            distance = int(_git(repo, "rev-list", "--count",
                                f"{candidate}..{ref_sha}").stdout)
            candidates.append((distance, candidate))
    if candidates:
        base_sha = min(candidates)[1]
    else:
        base_sha = _git(repo, "merge-base", ref_sha, lineage).stdout.strip()
    prompt_template = job.get("prompt") or ""
    takes_comparison = all(slot in prompt_template for slot in ("{base}", "{diff}"))
    if takes_comparison and args.stage == "review" and base_sha == ref_sha:
        _refuse("empty-comparison", f"{lineage} to differ from ref {ref_sha}",
                f"an empty comparison at {ref_sha}",
                f"name a ref on {lineage} with changes to review")
    job_id = mint_id(stage, args.harness)
    job_dir = root / job_id
    record_path = repo / ".dev/records/dispatches" / f"{job_id}.json"
    _preflight_isolation(args.harness, job_dir / "home" / args.harness, os.environ)
    if job_dir.exists() or record_path.exists():
        raise RuntimeError(f"dispatch id collision: {job_id}")
    job_dir.mkdir(parents=True)
    for name in ("in", "out"):
        (job_dir / name).mkdir()
    snapshot = job_dir / "snapshot"
    _snapshot(repo, lineage, ref_sha, snapshot)
    copied = []
    for source in args.input:
        src = Path(source)
        dest = job_dir / "in" / src.name
        if dest.exists():
            raise RuntimeError(f"duplicate input basename: {src.name}")
        shutil.copyfile(src, dest)
        copied.append({"path": str(dest), "sha256": _sha(dest)})
    diff_text = _git(repo, "diff", f"{base_sha}..{ref_sha}").stdout if base_sha != ref_sha else ""
    context_dir = job_dir / "context"
    context_dir.mkdir()
    context_diff = context_dir / "diff.patch"
    context_diff.write_text(diff_text)
    diff_file = job_dir / "diff.patch"
    diff_file.symlink_to(context_diff)
    diff_path = str(diff_file.resolve())
    values = {"ref": ref_sha, "base": base_sha, "diff": diff_path, "into": str(snapshot.resolve()),
              "out": str((job_dir / "out").resolve()),
              "inputs": " ".join(item["path"] for item in copied), "scope": args.scope or ""}
    prompt_text = _render(job["prompt"], values)
    prompt = job_dir / "prompt.txt"
    prompt.write_text(prompt_text)
    _iso_env, _flags, sandbox, containment, isolation_data = _harness_meta(args.harness, role, job_dir, os.environ)
    session = str(uuid.uuid4())
    wires = _module("wires", HARNESS_APP / "wires.py")
    tip = _git(repo, "rev-parse", lineage).stdout.strip()
    rec = record.build({
        "id": job_id, "lane": "dev", "stage": stage, "unit": args.unit or lineage,
        "lineage": {"branch": lineage, "base_sha": tip}, "follows": args.follows,
        "job": args.job, "role": role, "dispatched_by": agent,
        "at": {"launched": _now(), "closed": None},
        "snapshot": {"mode": "whole", "ref_name": args.ref, "ref_sha": ref_sha,
                     "behind_tip": (lambda n: n + 1 if n else 0)(int(
                         _git(repo, "rev-list", "--count", f"{ref_sha}..{lineage}").stdout)),
                     "root": str(snapshot.resolve())},
        "harness": {"name": args.harness, "version": "unknown", "isolation": isolation_data,
                    "sandbox": sandbox, "containment": containment,
                    "argv": _argv(args.harness, args.model, args.effort, session,
                                  prompt, _flags, sandbox)},
        "model": {"requested": args.model, "effort_requested": args.effort,
                  "ran": None, "read_from": None, "note": "no stream found"},
        "session": {"id": session, "stream": None, "stream_sha256_at_close": None},
        "brief": {"template": {"path": str(JOBS_PATH), "sha256": _sha(JOBS_PATH)},
                  "scope": args.scope, "inputs": copied, "sha256": _sha(prompt), "bytes": prompt.stat().st_size},
        "caps": {"cap-out": wires.budget(role), "source": "wires.py"},
        "overrides": overrides, "attempts": [], "result": None, "status": "launched"})
    _write_record(repo, rec)
    _run_attempt(rec, job_dir, job_caps=job.get("caps"))
    path, ran, found_id, mismatch = _stream(
        job_dir, args.harness, session, snapshot, ref_sha,
    )
    if path:
        if args.harness in {"codex", "grok"}:
            old_store = job_dir / "home" / args.harness / "sessions"
            new_store = job_dir / "home" / f"{args.harness}-stream" / "sessions"
            new_store.parent.mkdir(parents=True, exist_ok=True)
            if old_store.exists():
                shutil.move(str(old_store), str(new_store))
                path = new_store / path.relative_to(old_store)
        rec["session"].update(stream=str(path), stream_sha256_at_close=_sha(path))
        rec["model"].update(ran=ran, read_from=str(path))
        rec["model"].pop("note", None)
        if args.harness == "codex" and found_id:
            rec["session"]["id"] = found_id
    rec["session"]["spend"] = _spend(rec, path, job_dir)
    raw = (job_dir / "raw.out").read_bytes()
    runtime_note = rec.pop("_runtime_note", None)
    attempt_code = rec["attempts"][-1]["exit"]
    if runtime_note:
        env = _envelope(raw, args.job, ref_sha, runtime_note,
                        harness=args.harness, job_dir=job_dir, rec=rec)
        if runtime_note.startswith("trip"):
            env["status"] = "tripped"
    elif attempt_code:
        env = _envelope(raw, args.job, ref_sha,
                        f"harness-cli: exited {attempt_code}",
                        harness=args.harness, job_dir=job_dir, rec=rec)
    else:
        env = _envelope(raw, args.job, ref_sha, mismatch,
                        harness=args.harness, job_dir=job_dir, rec=rec)
    _settle(repo, record_path, rec, env)
    return 2 if runtime_note else 0


VENDOR = {"codex": "noreply@openai.com", "grok": "noreply@x.ai", "claude": "noreply@anthropic.com"}
# Attribution names per CONTRIB.md, keyed by the model id the stream reports.
# An unknown id is credited as itself rather than guessed.
MODEL_NAMES = {"gpt-5.6-sol": "GPT-5.6 Sol", "grok-4.6": "Grok 4.6",
               "claude-opus-5": "Claude Opus 5", "claude-fable-5": "Claude Fable 5",
               "claude-sonnet-5": "Claude Sonnet 5"}


def _commit_message(rec, env=None, job_dir=None):
    """The message the launcher commits with: the harness's own subject and
    body when it wrote out/COMMIT_MSG or its envelope carries
    `commit: {subject, body}`, else the generic line; either way the
    dispatch id and the attribution the CONTRIB template names (the
    model's display name, never its id)."""
    model = rec["model"].get("ran") or rec["model"]["requested"]
    name = MODEL_NAMES.get(model, model)
    vendor = VENDOR.get(rec["harness"]["name"], "noreply@unknown")
    commit = (env or {}).get("commit")
    msg_file = job_dir / "out" / "COMMIT_MSG" if job_dir else None
    if msg_file is not None and msg_file.is_file():
        text = msg_file.read_text(errors="replace").strip()
        if text:
            first, _, rest = text.partition("\n")
            commit = {"subject": first, "body": rest}
    subject = body = None
    kept_trailers = []
    if isinstance(commit, dict) and isinstance(commit.get("subject"), str) and commit["subject"].strip():
        subject = commit["subject"].strip().splitlines()[0]
        body = commit.get("body") if isinstance(commit.get("body"), str) else ""
        body_lines = []
        for line in body.strip().splitlines():
            # A trailer-shaped line anywhere but the final block is body
            # text to git and a refusal to the commit-msg hook (2026-08-29:
            # a codex COMMIT_MSG with `Reviewed-by:` mid-body was refused
            # and the generic subject landed). The launcher's own trailers
            # are replaced; every other trailer the harness wrote moves
            # into the final block, contiguous.
            if re.match(r"^Dispatch:", line):
                continue
            if re.match(r"^[A-Za-z][A-Za-z-]*: \S", line) and not line.startswith(("http", "Note:", "TODO:")):
                kept_trailers.append(line.strip())
                continue
            body_lines.append(line)
        body = "\n".join(body_lines).strip()
    if subject is None:
        subject = f"{rec['job']}: work of dispatch {rec['id']}"
        body = ""
    sources = [x for x in kept_trailers if x.startswith("Source:")]
    coauthors = [re.sub(r"^Co-authored-by:", "Co-Authored-By:", x) for x in kept_trailers
                 if x.lower().startswith("co-authored-by:")]
    others = [x for x in kept_trailers if not x.startswith("Source:") and not x.lower().startswith("co-authored-by:")]
    mine = f"Co-Authored-By: {name} <{vendor}>"
    # the running model's line replaces any the harness wrote for *its own*
    # vendor (an id or a wrong display name); other co-authors are kept
    coauthors = [c for c in coauthors if f"<{vendor}>" not in c]
    block = []
    for line in [*sources, "Source: original", *others, *coauthors, mine]:
        if line not in block:
            block.append(line)
    return (f"{subject}\n\n" + (f"{body}\n\n" if body else "")
            + f"Committed by the launcher: the {rec['harness']['name']} sandbox denies .git.\n"
            f"Dispatch: {rec['id']}\n\n" + "\n".join(block) + "\n")


def _commit_for_harness(snapshot, rec, env=None):
    """codex's sandbox keeps .git read-only, so a write job leaves its work
    uncommitted in the snapshot (2026-08-28: an implement job made the
    named test green and could not commit). The launcher commits it,
    crediting the model that ran, with the harness's own message when the
    envelope carries one (2026-08-29: five green codex runs landed under
    a generic subject and the conductor re-wrote each by hand)."""
    owner_env = dict(os.environ)
    owner_env.update(GIT_AUTHOR_NAME=OWNER_NAME, GIT_AUTHOR_EMAIL=OWNER_EMAIL,
                     GIT_COMMITTER_NAME=OWNER_NAME,
                     GIT_COMMITTER_EMAIL=OWNER_EMAIL)
    _git(snapshot, "add", "-A", env=owner_env)
    _git(snapshot, "commit", "-q", "-F", "-", env=owner_env,
         input=_commit_message(rec, env, snapshot.parent))


def _cause(rec, job_dir, env=None):
    """Why the harness stopped, in the order plan ebe2bb U4b.1 fixes:
    tripped → timeout → unsupervised → harness-cli:<code> → a permission
    prompt the turn never recovered from → the last event the session
    store holds. First match wins; a cancelled prompt is blamed only when
    it is the last permission event and nothing but phase/turn bookkeeping
    followed it (review ba0d93: a cancellation the turn recovered from
    must not be blamed)."""
    env = env or {}
    attempt = (rec.get("attempts") or [{}])[-1]
    marker = job_dir / "TRIPPED.md"
    if attempt.get("tripped") or env.get("status") == "tripped" or marker.exists():
        # The launcher writes TRIPPED.md for all three kills; only its own
        # first line says which (review ff6440 N3: `tripped` shadowed
        # timeout and unsupervised; N4: the harness's prose must never
        # decide the reason).
        own = marker.read_text(errors="replace").strip().splitlines()[0].lower() if marker.exists() else ""
        for word in ("timeout", "unsupervised"):
            if own.startswith(word) or f" {word}" in own[:80]:
                return {"reason": word, "note": own or None}
        return {"reason": "tripped", "note": own or None}
    if attempt.get("exit"):
        return {"reason": f"harness-cli:{attempt['exit']}"}
    if rec.get("_envelope_cause"):
        return {"reason": rec.pop("_envelope_cause")}
    harness = rec["harness"]["name"]
    stream = (rec.get("session") or {}).get("stream")
    events = None
    if harness == "grok":
        candidates = []
        if stream and Path(stream).is_file():
            candidates.append(Path(stream).parent / "events.jsonl")
        candidates += sorted((job_dir / "home").glob("grok-stream/**/events.jsonl"))
        candidates += sorted((job_dir / "home").glob("grok/**/events.jsonl"))
        events = next((c for c in candidates if c.is_file()), None)
    elif stream and Path(stream).is_file():
        events = Path(stream)
    if events is None:
        return {"reason": "no-session-store"}
    last = None
    last_permission = None
    after_permission = []
    for line in events.read_text(errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        last = item
        if item.get("type") == "permission_resolved":
            last_permission = item
            after_permission = []
        elif last_permission is not None:
            after_permission.append(item.get("type"))
    # A cancelled tool emits its own tool_result row; that is the cancel,
    # not recovery (review ff6440 N2). Recovery is later work: another
    # permission request, an assistant turn, a tool call.
    if after_permission and after_permission[0] == "tool_result":
        after_permission = after_permission[1:]
    bookkeeping = ("phase_changed", "turn_ended", "session_end", "tool_result")
    if (last_permission is not None
            and last_permission.get("decision") not in (None, "approved", "allowed", "allow")
            and all(kind in bookkeeping for kind in after_permission)):
        return {"reason": f"permission-{last_permission.get('decision')}",
                "tool": last_permission.get("tool_name"),
                "wait_ms": last_permission.get("wait_ms"), "at": last_permission.get("ts")}
    if last is None:
        return {"reason": "empty-session-store", "path": str(events)}
    return {"reason": "last-event", "type": last.get("type"),
            "at": last.get("ts") or last.get("timestamp")}


def _spend(rec, stream, job_dir):
    """`session.spend` from the harness's own store, read by
    telemetry/usage.py (the same parsers the usage report uses). A value
    or `{unresolved: <why>}` — never a zero (U5; 2026-08-29: 105 of 105
    records carried `spend: null`, so no cost could be measured)."""
    harness = rec["harness"]["name"]
    session = (rec.get("session") or {}).get("id")
    if not stream:
        return {"unresolved": "no session stream discovered"}
    try:
        usage = _module("usage", TELEMETRY_APP / "usage.py")
    except (OSError, ImportError, SyntaxError) as exc:
        return {"unresolved": f"usage.py not loadable: {exc}"}
    stream = Path(stream)
    try:
        if harness == "claude":
            root = stream.parent.parent
            sessions = list(usage.claude_sessions(root, None))
        elif harness == "codex":
            root = job_dir / "home" / "codex-stream"
            if not root.exists():
                root = job_dir / "home" / "codex"
            sessions = list(usage.codex_sessions(root, None))
        elif harness == "grok":
            root = job_dir / "home" / "grok-stream"
            if not root.exists():
                root = job_dir / "home" / "grok"
            sessions = list(usage.grok_sessions(root, None))
        else:
            return {"unresolved": f"no usage parser for {harness}"}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"unresolved": f"usage parse failed: {type(exc).__name__}: {exc}"}
    match = next((s for s in sessions if isinstance(s, dict) and str(s.get("session")) == str(session)), None)
    if match is None:
        return {"unresolved": f"session {session} not in the store ({len(sessions)} sessions read)"}
    tokens = match.get("tokens")
    if not isinstance(tokens, dict) or not any(isinstance(v, (int, float)) for v in tokens.values()):
        return {"unresolved": "the store carries no usage for this session", "source": str(stream)}
    spend = {k: tokens.get(k) for k in ("input", "cached", "output", "total") if tokens.get(k) is not None}
    if match.get("cost_usd_ticks") is not None:
        spend["cost_usd_ticks"] = match["cost_usd_ticks"]
    if match.get("incomplete"):
        spend["incomplete"] = True
    spend["source"] = str(stream)
    spend["messages"] = match.get("messages")
    return spend


def _write_residual_patch(snapshot, job_dir):
    """Preserve what a harness left uncommitted — staged, unstaged and
    untracked alike — as one patch `git apply` accepts, so a retry can take
    it as an input instead of the conductor diffing the snapshot by hand.
    A temporary index keeps the snapshot's own index untouched."""
    import tempfile
    untracked = _git(snapshot, "ls-files", "--others", "--exclude-standard", check=False).stdout.splitlines()
    with tempfile.NamedTemporaryFile(prefix="residual-index-", delete=False) as tmp:
        index = tmp.name
    env = dict(os.environ, GIT_INDEX_FILE=index)
    try:
        _git(snapshot, "read-tree", "HEAD", env=env)
        excludes = [f":(exclude,glob)**/{seg.rstrip('/')}/**" for seg in CACHE_RESIDUAL]
        _git(snapshot, "add", "-A", "--", ".", *excludes, env=env)
        patch = _git(snapshot, "diff", "--cached", "--binary", "HEAD", env=env, check=False).stdout
    finally:
        Path(index).unlink(missing_ok=True)
    out = job_dir / "residual.patch"
    out.write_text(patch)
    return {"path": str(out), "sha256": _sha(out), "untracked": untracked}


# Tool caches a read role leaves behind by running the suite or ruff are
# not writes into the tree; everything else is.
CACHE_RESIDUAL = ("__pycache__/", ".ruff_cache/", ".pytest_cache/", ".mypy_cache/")


def _residual(snapshot):
    lines = _git(snapshot, "status", "--porcelain=v1", "-uall").stdout.splitlines()
    return [line for line in lines
            if not any(seg in line[3:] for seg in CACHE_RESIDUAL)]


def _settle(repo, record_path, rec, env):
    """Collect the snapshot's state, finalize the record, commit it."""
    snapshot = Path(rec["snapshot"]["root"])
    ref_sha = rec["snapshot"]["ref_sha"]
    role, job_id = rec["role"], rec["id"]
    residual = _residual(snapshot)
    tripped = bool(rec.get("attempts") and rec["attempts"][-1].get("tripped"))
    if role == "write" and rec["harness"]["name"] == "codex" and residual and not tripped:
        _commit_for_harness(snapshot, rec, env)
        residual = _residual(snapshot)
    residual_patch = _write_residual_patch(snapshot, Path(rec["snapshot"]["root"]).parent) if residual else None
    head = _git(snapshot, "rev-parse", "HEAD").stdout.strip()
    changed = _git(snapshot, "diff", "--name-only", f"{ref_sha}..{head}").stdout.splitlines()
    if role == "read" and head != ref_sha:
        _invalidate_envelope(env, "read-role-head: HEAD must equal ref_sha")
    elif role == "write" and _git(snapshot, "merge-base", "--is-ancestor", ref_sha, head, check=False).returncode:
        _invalidate_envelope(env, "off-lineage-head: HEAD does not descend from ref_sha")
    elif role == "write":
        _git(repo, "-c", "core.logAllRefUpdates=always", "fetch",
             "--no-write-fetch-head", str(snapshot), f"{head}:refs/dispatch/{job_id}")
    rec["result"] = {"head": head, "changed_paths": changed,
                     "residual_paths": residual, "envelope": env}
    if residual_patch:
        rec["result"]["residual_patch"] = residual_patch
    cause = _cause(rec, Path(rec["snapshot"]["root"]).parent, env)
    rec.pop("_envelope_cause", None)
    rec["result"]["cause"] = cause
    if env.get("status") == "invalid" and str(env.get("note", "")).startswith("envelope-parse"):
        env["note"] = f"{env['note']}; cause: {json.dumps(cause, sort_keys=True)}"
    rec["at"]["closed"] = _now()
    rec["status"] = "closed"
    _write_record(repo, rec)
    _commit_record(repo, record_path, rec)


def _status(args):
    root = _jobs_root()
    dirs = [root / args.id] if args.id else sorted(p for p in root.glob("*") if p.is_dir())
    rows = []
    for d in dirs:
        if not d.exists():
            state = "unlaunched"
        elif (d / "TRIPPED.md").exists():
            state = "tripped"
        elif (d / "exit").exists():
            state = "finished"
        else:
            try:
                pid = json.loads((d / "state.json").read_text()).get("pid")
                os.kill(int(pid), 0)
                state = "running"
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                state = "DIED"
        rows.append({"id": d.name, "status": state})
    payload = rows[0] if args.id and rows else rows
    print(json.dumps(payload) if args.json else "\n".join(f"{r['id']} {r['status']}" for r in rows))
    return 0


def _find_record(repo, job_id):
    path = repo / ".dev/records/dispatches" / f"{job_id}.json"
    if not path.is_file():
        raise RuntimeError(f"record not found: {job_id}")
    return path, json.loads(path.read_text())


def _resume(args):
    repo = _repo()
    _path, rec = _find_record(repo, args.id)
    job_dir = _jobs_root() / args.id
    prior_envelope = (rec.get("result") or {}).get("envelope") or {}
    settled_refusal = None
    if rec.get("status") == "closed" and prior_envelope.get("status") == "invalid":
        settled_refusal = {
            key: prior_envelope.get(key) for key in ("status", "verdict", "note")
        }
    if (job_dir / "TRIPPED.md").exists() and not args.reason and args.cap_out is None:
        raise RuntimeError("tripped job requires a changed cap or reason")
    if args.cap_out is not None:
        rec["caps"]["cap-out"] = args.cap_out
        rec["caps"]["source"] = "resume"
    jobs = json.loads(Path(JOBS_PATH).read_text())
    _run_attempt(rec, job_dir, job_caps=jobs[rec["job"]].get("caps"),
                 resume=True, reason=args.reason)
    runtime_note = rec.pop("_runtime_note", None)
    if runtime_note:
        envelope = (rec.get("result") or {}).get("envelope") or {}
        envelope.update(status="tripped" if runtime_note.startswith("trip") else "invalid",
                        verdict=None, note=runtime_note)
        rec.setdefault("result", {})["envelope"] = envelope
    else:
        raw = (job_dir / "raw.out").read_bytes()
        code = rec["attempts"][-1]["exit"]
        note = f"harness-cli: exited {code}" if code else None
        stream, ran, found_id, mismatch = _stream(
            job_dir, rec["harness"]["name"], rec["session"]["id"],
            Path(rec["snapshot"]["root"]), rec["snapshot"]["ref_sha"],
        )
        if stream and rec["harness"]["name"] in {"codex", "grok"}:
            old_store = job_dir / "home" / rec["harness"]["name"] / "sessions"
            new_store = job_dir / "home" / f"{rec['harness']['name']}-stream" / "sessions"
            if old_store.exists():
                new_store.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(old_store, new_store, dirs_exist_ok=True)
                stream = new_store / stream.relative_to(old_store)
                shutil.rmtree(old_store)
        if stream:
            rec["session"].update(
                stream=str(stream), stream_sha256_at_close=_sha(stream),
            )
            rec["model"].update(ran=ran, read_from=str(stream))
            rec["model"].pop("note", None)
            if rec["harness"]["name"] == "codex" and found_id:
                rec["session"]["id"] = found_id
        rec["session"]["spend"] = _spend(rec, stream, job_dir)
        if mismatch:
            note = f"{note}; {mismatch}" if note else mismatch
        env = _envelope(raw, rec["job"], rec["snapshot"]["ref_sha"], note,
                        harness=rec["harness"]["name"], job_dir=job_dir,
                        rec=rec)
        if settled_refusal is not None:
            env.update(settled_refusal)
        rec.setdefault("result", {})["envelope"] = env
        rec["result"]["cause"] = _cause(rec, job_dir, env)
    _write_record(repo, rec)
    return 0


def _close(args):
    repo = _repo()
    path, rec = _find_record(repo, args.id)
    branch = _branch(repo)
    lineage = rec["lineage"]["branch"]
    if branch != lineage:
        _refuse("record-target", f"the checkout on {lineage}", branch, f"run from a worktree of {lineage}")
    job_dir = _jobs_root() / args.id
    state = json.loads((job_dir / "state.json").read_text())
    try:
        os.kill(int(state["pid"]), 0)
        died = False
    except (OSError, TypeError, ValueError):
        died = not (job_dir / "exit").exists()
    if died:
        raw_path = job_dir / "raw.out"
        raw = raw_path.read_bytes() if raw_path.is_file() else b""
        env = _envelope(raw, rec["job"], rec["snapshot"]["ref_sha"],
                        "DIED: process vanished without exit",
                        harness=rec["harness"]["name"], job_dir=job_dir,
                        rec=rec)
        rec["result"] = {"head": rec["snapshot"]["ref_sha"], "changed_paths": [],
                         "residual_paths": [], "envelope": env}
        rec["status"] = "died"
        rec["at"]["closed"] = _now()
        _write_record(repo, rec)
        _commit_record(repo, path, rec)
        return 0
    if (job_dir / "exit").exists() and not rec.get("result"):
        # The harness finished and wrote its exit; the launcher did not get
        # to collect (it crashed on 2026-08-28 parsing a string stamp). The
        # output is still in raw.out, so collect it now rather than lose it.
        code = int((job_dir / "exit").read_text().strip() or 0)
        raw = (job_dir / "raw.out").read_bytes()
        note = f"harness-cli: exited {code}" if code else None
        if (job_dir / "TRIPPED.md").exists():
            note = "trip: battery tripped"
        stream, ran, found_id, mismatch = _stream(
            job_dir, rec["harness"]["name"], rec["session"]["id"],
            Path(rec["snapshot"]["root"]), rec["snapshot"]["ref_sha"],
        )
        if stream:
            rec["session"].update(
                stream=str(stream), stream_sha256_at_close=_sha(stream),
            )
            rec["model"].update(ran=ran, read_from=str(stream))
            rec["model"].pop("note", None)
            if rec["harness"]["name"] == "codex" and found_id:
                rec["session"]["id"] = found_id
        if mismatch:
            note = f"{note}; {mismatch}" if note else mismatch
        env = _envelope(raw, rec["job"], rec["snapshot"]["ref_sha"], note,
                        harness=rec["harness"]["name"], job_dir=job_dir,
                        rec=rec)
        if not rec["attempts"]:
            rec["attempts"].append({"n": 1, "launched": rec["at"]["launched"],
                                    "ended": _now(), "exit": code,
                                    "tripped": (job_dir / "TRIPPED.md").exists()})
        _settle(repo, path, rec, env)
    return 0


def _brief(args):
    repo = _repo()
    _path, rec = _find_record(repo, args.check)
    prompt = _jobs_root() / args.check / "prompt.txt"
    ok = prompt.is_file() and _sha(prompt) == rec["brief"]["sha256"]
    print("brief matches" if ok else "brief digest mismatch")
    return 0 if ok else 1


def _parser():
    p = argparse.ArgumentParser(prog="launch.py")
    sub = p.add_subparsers(dest="verb")
    s = sub.add_parser("status")
    s.add_argument("id", nargs="?")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("resume")
    r.add_argument("id")
    r.add_argument("--prompt-file")
    r.add_argument("--reason")
    r.add_argument("--cap-out", type=int)
    c = sub.add_parser("close")
    c.add_argument("id")
    b = sub.add_parser("brief")
    b.add_argument("--check", required=True)
    return p


def _launch_parser():
    p = argparse.ArgumentParser(prog="launch.py")
    p.add_argument("job")
    p.add_argument("--harness", required=True)
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--ref", required=True)
    p.add_argument("--lineage")
    p.add_argument("--unit")
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--scope")
    p.add_argument("--input", action="append", default=[])
    p.add_argument("--follows", action="append", default=[])
    p.add_argument("--override", action="append", default=[])
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] and argv[0] in {"status", "resume", "close", "brief"}:
            args = _parser().parse_args(argv)
            return {"status": _status, "resume": _resume, "close": _close, "brief": _brief}[args.verb](args)
        return _launch(_launch_parser().parse_args(argv))
    except Refused as exc:
        print(f"launch.py: refusal: {exc}", file=sys.stderr)
        return REFUSAL
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"launch.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
