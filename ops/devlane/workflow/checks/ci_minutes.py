#!/usr/bin/env python3
"""What GitHub Actions billed this repository, from the runs API.

On a private repository every job bills a whole minute, rounded up,
and a job that ran for six seconds bills the same minute as one that
ran for fifty-nine. Nothing in the tree says what a push costs, so the
number that finally mattered -- the organisation's included minutes --
was learned from GitHub refusing to start jobs, not from a check.

    ci_minutes.py [--repo OWNER/NAME] [--days N | --since YYYY-MM-DD]
                  [--budget MINUTES] [--json] [--dump FILE]
    ci_minutes.py --input FILE [--budget MINUTES] [--json]

Reads every workflow run created in the window, then every run's jobs,
and bills each job the way GitHub does: ceil(seconds / 60); nothing for
a job that never reached a runner (no steps); nothing for a job that ran
on a self-hosted runner (its `labels` carry `self-hosted`), because
GitHub meters hosted minutes only. Prints the
window's totals, the billed minutes per distinct pushed commit -- the
figure a change to `.github/workflows/` must state before and after --
and a per-workflow table.

Exit 0 when under or at `--budget` (or when no budget was given), 1
when over it. Exit 2 -- UNREACHABLE, with no figures printed -- when
the API could not be read: `gh` missing, a non-zero exit, a body that
is not the JSON expected. A window that read cleanly and holds zero
runs is a genuine zero and prints as one; a window that could not be
read is not a zero and never prints as one.

`--dump` saves the fetched payload; `--input` replays one, which is how
the tests feed planted windows without a network and how a figure in a
commit body can be re-derived later from the bytes it was measured on.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

API = "repos/{repo}/actions/runs?per_page=100&created=>={since}"
JOBS = "repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"

EXIT_OK = 0
EXIT_OVER = 1
EXIT_UNREACHABLE = 2
EXIT_USAGE = 64


class Unreachable(Exception):
    """The API was not read. There is no figure, and none is printed."""


def _iso(value):
    return datetime.fromisoformat(value)


def self_hosted(job):
    return any(str(label).lower() == "self-hosted" for label in job.get("labels") or [])


def billed_minutes(job):
    """GitHub's rounding: whole minutes, up; nothing for a job that
    never reached a runner; nothing for a job a self-hosted runner ran,
    whose seconds are still real and still reported."""
    if not isinstance(job, dict):
        raise Unreachable(
            f"job: expected a mapping, found {type(job).__name__}"
        )
    if not job.get("steps"):
        return 0, 0.0
    started, completed = job.get("started_at"), job.get("completed_at")
    if not started or not completed:
        raise Unreachable(
            "job: expected started_at and completed_at for a job with steps, "
            f"found started_at={started!r}, completed_at={completed!r}"
        )
    seconds = max(0.0, (_iso(completed) - _iso(started)).total_seconds())
    if self_hosted(job):
        return 0, seconds
    return math.ceil(seconds / 60), seconds


def family(job_name):
    """`dev: gates (lint)` and `dev: gates (imports)` are one family."""
    return job_name.split(" (", 1)[0]


def summarize(payload):
    """Totals for a fetched or replayed payload, as one JSON-able dict."""
    runs = payload.get("runs")
    jobs_by_run = payload.get("jobs")
    if not isinstance(runs, list) or not isinstance(jobs_by_run, dict):
        raise Unreachable(
            "payload: expected {runs: [...], jobs: {run_id: [...]}}, found "
            f"keys {sorted(payload) if isinstance(payload, dict) else type(payload).__name__}"
        )
    by_workflow = collections.defaultdict(
        lambda: {"runs": 0, "jobs": 0, "billed": 0, "real_seconds": 0.0,
                 "shas": set()}
    )
    by_family = collections.defaultdict(
        lambda: {"jobs": 0, "billed": 0, "real_seconds": 0.0}
    )
    by_event = collections.Counter()
    shas = set()
    jobs = billed = never_started = hosted_by_us = 0
    real = hosted_real = 0.0
    for run in runs:
        if not isinstance(run, dict):
            raise Unreachable(
                f"run: expected a mapping, found {type(run).__name__}"
            )
        run_id = str(run.get("id"))
        if run_id not in jobs_by_run:
            raise Unreachable(f"run {run_id}: jobs were not fetched")
        workflow = str(run.get("path", "?")).rsplit("/", 1)[-1]
        sha = str(run.get("head_sha", ""))[:7]
        shas.add(sha)
        wf = by_workflow[workflow]
        wf["runs"] += 1
        wf["shas"].add(sha)
        for job in jobs_by_run[run_id]:
            minutes, seconds = billed_minutes(job)
            if not job.get("steps"):
                never_started += 1
            elif self_hosted(job):
                hosted_by_us += 1
            jobs += 1
            billed += minutes
            real += seconds
            if not self_hosted(job):
                hosted_real += seconds
            wf["jobs"] += 1
            wf["billed"] += minutes
            wf["real_seconds"] += seconds
            fam = by_family[family(str(job.get("name", "?")))]
            fam["jobs"] += 1
            fam["billed"] += minutes
            fam["real_seconds"] += seconds
            by_event[str(run.get("event", "?"))] += minutes
    workflows = {}
    for name, row in sorted(by_workflow.items()):
        pushes = len(row["shas"])
        workflows[name] = {
            "runs": row["runs"],
            "pushes": pushes,
            "jobs": row["jobs"],
            "billed": row["billed"],
            "real": round(row["real_seconds"] / 60, 1),
            "billed_per_push": round(row["billed"] / pushes, 1) if pushes else 0.0,
        }
    families = {
        name: {"jobs": row["jobs"], "billed": row["billed"],
               "real": round(row["real_seconds"] / 60, 1)}
        for name, row in sorted(
            by_family.items(), key=lambda item: -item[1]["billed"]
        )
    }
    pushes = len(shas)
    return {
        "runs": len(runs),
        "pushes": pushes,
        "jobs": jobs,
        "never_started": never_started,
        "self_hosted": hosted_by_us,
        "real": round(real / 60, 1),
        "billed": billed,
        "rounding_share": (
            round(1 - (hosted_real / 60) / billed, 2) if billed else 0.0
        ),
        "billed_per_push": round(billed / pushes, 1) if pushes else 0.0,
        "by_event": dict(by_event),
        "workflows": workflows,
        "families": families,
    }


def render(summary, window, budget):
    lines = [
        f"CI minutes, {window}",
        "",
        (
            "| runs | pushes | jobs | never started | self-hosted | real min "
            "| billed min | rounding | billed/push |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {summary['runs']} | {summary['pushes']} | {summary['jobs']} "
            f"| {summary['never_started']} | {summary['self_hosted']} "
            f"| {summary['real']} | {summary['billed']} "
            f"| {int(summary['rounding_share'] * 100)}% | {summary['billed_per_push']} |"
        ),
        "",
        "| workflow | runs | pushes | jobs | real min | billed min | billed/push |",
        "|:--|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in summary["workflows"].items():
        lines.append(
            f"| {name} | {row['runs']} | {row['pushes']} | {row['jobs']} "
            f"| {row['real']} | {row['billed']} | {row['billed_per_push']} |"
        )
    lines += ["", "| job family | jobs | real min | billed min |", "|:--|---:|---:|---:|"]
    for name, row in summary["families"].items():
        lines.append(f"| {name} | {row['jobs']} | {row['real']} | {row['billed']} |")
    if budget is not None:
        verdict = "OVER" if summary["billed"] > budget else "within"
        lines += ["", f"budget {budget} min: {verdict} ({summary['billed']} billed)"]
    return "\n".join(lines) + "\n"


def gh_json(gh, path, paginate=False):
    argv = [gh, "api", path]
    if paginate:
        argv += ["--paginate", "--slurp"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as err:
        raise Unreachable(f"{gh}: {err}") from err
    if proc.returncode != 0:
        raise Unreachable(
            f"gh api {path}: exit {proc.returncode}: {proc.stderr.strip()[:300]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as err:
        raise Unreachable(f"gh api {path}: body is not JSON: {err}") from err


def fetch(gh, repo, since):
    pages = gh_json(gh, API.format(repo=repo, since=since), paginate=True)
    runs = []
    for page in pages if isinstance(pages, list) else [pages]:
        if not isinstance(page, dict) or "workflow_runs" not in page:
            raise Unreachable("runs: expected pages with workflow_runs, found "
                              f"{type(page).__name__}")
        runs.extend(page["workflow_runs"])

    def one(run):
        body = gh_json(gh, JOBS.format(repo=repo, run_id=run["id"]))
        if not isinstance(body, dict) or "jobs" not in body:
            raise Unreachable(f"run {run['id']}: expected jobs, found "
                              f"{type(body).__name__}")
        jobs = body["jobs"]
        total = body.get("total_count")
        if not isinstance(jobs, list) or not isinstance(total, int):
            raise Unreachable(
                f"run {run['id']}: expected jobs list and integer total_count"
            )
        if len(jobs) != total:
            raise Unreachable(
                f"run {run['id']}: expected {total} jobs, fetched {len(jobs)}; "
                "the jobs page is incomplete"
            )
        return str(run["id"]), body

    with ThreadPoolExecutor(max_workers=8) as pool:
        job_bodies = dict(pool.map(one, runs))
    return {
        "repo": repo,
        "since": since,
        "runs": runs,
        "jobs": {run_id: body["jobs"] for run_id, body in job_bodies.items()},
        "api": {"runs": pages, "jobs": job_bodies},
    }


def repo_from_gh(gh):
    body = gh_json(gh, "repos/{owner}/{repo}")
    if not isinstance(body, dict) or not body.get("full_name"):
        raise Unreachable("repos/{owner}/{repo}: no full_name in the body")
    return body["full_name"]


def parse(argv):
    parser = argparse.ArgumentParser(prog="ci_minutes.py", add_help=True)
    parser.add_argument("--repo")
    parser.add_argument("--gh", default="gh")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--days", type=int)
    window.add_argument("--since")
    parser.add_argument("--budget", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dump")
    parser.add_argument("--input")
    return parser.parse_args(argv)


def main(argv) -> int:
    try:
        args = parse(argv[1:])
    except SystemExit as err:
        return EXIT_USAGE if err.code else EXIT_OK
    if args.budget is not None and args.budget < 0:
        print("--budget: expected a non-negative number of minutes, found "
              f"{args.budget}", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.input:
            try:
                with open(args.input, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError) as err:
                raise Unreachable(f"--input {args.input}: {err}") from err
            window = f"replayed from {args.input}"
        else:
            if args.since:
                since = args.since
            else:
                days = 7 if args.days is None else args.days
                if days <= 0:
                    print(f"--days: expected a positive number, found {days}",
                          file=sys.stderr)
                    return EXIT_USAGE
                since = (datetime.now(UTC) - timedelta(days=days)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            repo = args.repo or repo_from_gh(args.gh)
            payload = fetch(args.gh, repo, since)
            window = f"{repo} since {since} (UTC)"
            if args.dump:
                try:
                    with open(args.dump, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle)
                except OSError as err:
                    raise Unreachable(f"--dump {args.dump}: {err}") from err
        summary = summarize(payload)
    except Unreachable as err:
        print(f"UNREACHABLE: {err}", file=sys.stderr)
        return EXIT_UNREACHABLE
    summary["window"] = window
    summary["budget"] = args.budget
    if args.json:
        json.dump(summary, sys.stdout)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(summary, window, args.budget))
    if args.budget is not None and summary["billed"] > args.budget:
        return EXIT_OVER
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
