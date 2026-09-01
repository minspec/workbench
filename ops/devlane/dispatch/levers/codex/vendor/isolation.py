"""Strip the operator's personal setup out of a dispatched harness.

A harness launched from a developer's machine does not start empty. It
discovers instruction files, hooks, skills and MCP servers from that
person's home directory, and none of that is this project's law. It is
not in the repository, no other harness shares it, CI does not have it,
and nobody agreed that it applies to work done here.

What was measured on 2026-08-22, dispatching into a throwaway snapshot
that contained nothing but the brief:

  claude  the operator's ~/.claude/CLAUDE.md was in the system prompt.
          Asked "do your instructions contain <a phrase from it>", a
          default dispatch answered YES and an isolated one answered NO.
          A SessionStart hook, the personal skill listing and personal
          MCP servers arrived as attachments.
  codex   ~/.codex/hooks.json ran a SessionStart command living in an
          unrelated repository, and its output was injected.
  grok    `grok inspect` listed ~/.claude/CLAUDE.md as a project
          instruction worth ~7012 tokens, plus a global rules file and
          31 skills, 24 of them the operator's.

So the leak is not one harness's quirk. It is what all three do by
design, and the fix has to be per-harness because each reads a
different place.

Two mechanisms are used here, whichever the harness supports:

  flags   the harness offers a documented way to not load user-scoped
          configuration. Cheapest and least invasive; nothing on disk
          is touched.
  home    the harness only looks in a directory named by an
          environment variable, so it is pointed at a directory built
          here that holds credentials and nothing else.

CREDENTIALS ARE THE ONE EXCEPTION, and it is deliberate. A harness
that cannot authenticate cannot run at all, so the minimal home links
the auth file through and nothing else. Everything that carries
instructions, behaviour or context is left behind. `auth_files` is the
complete list of what crosses that line; it is data, so it can be
read, and a test asserts nothing else is ever linked.

WHAT THIS IS NOT. Every entry below closes a discovery path that was
found by looking. That makes this a list of known leaks, and a list of
known leaks is only ever as current as the last time somebody looked
-- a harness release can add a fourth path, and nothing here would
say so. `probe.py` exists for exactly that reason and should be run
when a harness version changes.

The guarantee that does not depend on having enumerated correctly is a
container with no operator home mounted in it: then there is nothing
to discover, whatever the harness decides to look for next. That is
the backstop if a leak is found with no environment variable behind
it, or if a harness stops honouring one. This module is the cheap
version and it is honest about being the cheap version.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# --------------------------------------------------------------------
# The data. Every entry says how a harness is isolated and how that was
# established. An entry with no mechanism is not a harness that happens
# to be clean -- it is one nobody has checked, and dispatching it is
# refused rather than assumed safe.
# --------------------------------------------------------------------

HARNESSES = {
    "claude": {
        "mechanism": "flags",
        "flags": [
            # Drops ~/.claude/settings.json AND user-scoped CLAUDE.md.
            "--setting-sources", "project,local",
            # Drops MCP servers configured in the operator's account.
            "--strict-mcp-config",
            # Drops the personal skill listing.
            "--disable-slash-commands",
        ],
        "home_env": None,
        "auth_files": [],
        # A phrase that appears only in the operator's own doctrine
        # file. Asking the model whether it can see this is the only
        # way to observe the system prompt, which is not written to
        # the session trace -- absence from the trace would prove
        # nothing.
        "probe_phrase": "Start from the class, not the instance",
        # HOME is untouched, so traces stay in the real home.
        "sessions": {"under": "real", "path": ".claude/projects"},
        "measured": {
            "on": "2026-08-22",
            "version": "unrecorded",
            "probe_default": "YES",
            "probe_isolated": "NO",
            "attachment_bytes_default": 17304,
            "attachment_bytes_isolated": 2761,
            "note": "what remains is Claude Code's own built-in machinery",
        },
    },
    "codex": {
        "mechanism": "home",
        "flags": [],
        "home_env": "CODEX_HOME",
        "auth_files": ["auth.json"],
        "probe_phrase": None,
        # CODEX_HOME moved, so the trace moves with it.
        "sessions": {"under": "minimal", "path": "sessions"},
        "measured": {
            "on": "2026-08-22",
            "version": "0.148.0",
            "leak": "~/.codex/hooks.json SessionStart ran "
                    "projects/xormania/xor/tools/xortations/hooks/session_start.py",
            "note": "personal MCP servers and a memories store also live under the home",
        },
    },
    "grok": {
        # Two variables, because two different directories leak. GROK_HOME
        # alone still let ~/.claude/CLAUDE.md through: grok looks for that
        # under $HOME, not under its own home. HOME alone still let
        # ~/.grok/rules through. Both, or neither works.
        "mechanism": "home",
        "flags": [],
        "home_env": "GROK_HOME",
        "also_env": ["HOME"],
        "auth_files": ["auth.json"],
        "probe_phrase": None,
        "sessions": {"under": "minimal", "path": "sessions"},
        "measured": {
            "on": "2026-08-22",
            "version": "1.0.5",
            "leak": "grok inspect listed ~/.claude/CLAUDE.md (~7012 tokens) "
                    "and ~/.grok/rules/00-xortations-first-turn.md (~161 tokens) "
                    "as project instructions; 31 skills, 24 user-scoped",
            "note": "HOME alone drops CLAUDE.md and cuts skills 31 -> 7; "
                    "GROK_HOME alone drops the rules file; both are needed",
        },
    },
}


class NotIsolated(Exception):
    """Raised instead of dispatching a harness that cannot be isolated.

    The refusal is the point. A harness absent from HARNESSES has not
    been shown to be clean, and defaulting to "launch it anyway"
    converts "nobody looked" into "we checked and it was fine".
    """


def _real_home(harness, env, given=True):
    """Where this harness's real config lives, for reading credentials.

    Two corrections, both from a test author who could not see this
    function and reasoned from what it PROMISES (PR #40 follow-up):

    `home_env` is honoured when the environment sets it. An operator who
    runs codex with CODEX_HOME set keeps their config there, not in
    ~/.codex, so reading the wrong directory would report a credential
    absent that is present -- or link one that is not the one in use.

    `given` defaults to True -- the strict reading -- so a direct caller
    gets the refusal and only `build_home`, which knows whether its
    caller supplied an env, may ask for the lenient one.

    And when the caller passed an environment EXPLICITLY, a missing HOME
    is refused rather than filled in from `Path.home()`. Passing an env
    is how a caller says "this, and nothing of mine"; reaching past it
    to the operator's real home is the leak this module exists to
    prevent, and it made one of the author's tests pass on this machine
    and fail on a clean one -- the shape of a suite that lies.
    """
    spec = HARNESSES[harness]
    named = spec.get("home_env")
    if named and env.get(named):
        return Path(env[named])
    home = env.get("HOME")
    # EMPTY IS ABSENT. `HOME=""` is not None, so the refusal below did
    # not fire, and `Path("") / ".codex"` is the RELATIVE path `.codex`
    # — resolved against whatever directory the launcher happened to be
    # in, which is ambient project state and exactly what an explicit
    # environment is supposed to exclude (Copilot, PR #42). The same
    # distinction this repo makes everywhere else between "we looked and
    # found none" and "nobody looked", arriving one more time as a
    # falsy value that is not None.
    if not home:
        if given:
            raise NotIsolated(
                f"{harness}: the environment passed here sets neither "
                f"{named or 'HOME'} nor HOME, so there is nowhere to read "
                f"credentials from. Falling back to the operator's own home "
                f"would be the leak this builds a home to prevent.")
        home = str(Path.home())
    return Path(home) / f".{harness}"


def build_home(harness, root, env=None):
    """Create a minimal home for `harness` under `root`; return its path.

    It holds the credential files named in `auth_files` and nothing
    else. Each is symlinked, not copied, so a credential is never
    duplicated into a scratch directory that outlives the run.

    Raises NotIsolated when a credential the harness needs is missing,
    rather than producing a home that will fail to authenticate in a
    way that looks like a model refusal.

    `root` must be missing or an EMPTY directory. A reused one may carry
    the operator's own setup, and preserving it is the leak this builds
    a home to prevent -- so a populated root is refused, naming what it
    found. That was not written down until an independent test author
    assumed the opposite and expected a rebuild.
    """
    given = env is not None
    env = os.environ if env is None else env
    spec = HARNESSES.get(harness)
    if spec is None:
        raise NotIsolated(
            f"{harness!r} has no isolation entry: nobody has established "
            f"what it loads from the operator's home, so it is not "
            f"dispatched. Add an entry with a measurement.")
    dest = Path(root)
    # A MINIMAL home has to start empty. `exist_ok=True` on a reused
    # root preserved whatever was already there and returned normally,
    # so a home carrying the operator's hooks.json, AGENTS.md and
    # skills/ could be handed to a dispatch — through the constructor
    # of the module that exists to strip exactly those (Codex, PR #40).
    # The structural probe is the only reader that would notice, and
    # nothing on the launch path calls it.
    if dest.exists():
        if not dest.is_dir():
            raise NotIsolated(
                f"{harness}: {dest} exists and is not a directory; a "
                f"minimal home cannot be built there.")
        leftovers = sorted(p.name for p in dest.iterdir())
        if leftovers:
            raise NotIsolated(
                f"{harness}: {dest} is not empty ({', '.join(leftovers[:5])}"
                f"{', …' if len(leftovers) > 5 else ''}). A reused home may "
                f"carry the operator's own setup, which is the leak this "
                f"builds a home to prevent. Pass a fresh directory.")
    dest.mkdir(parents=True, exist_ok=True)
    src_home = _real_home(harness, env, given)
    for name in spec["auth_files"]:
        src = src_home / name
        if not src.exists():
            raise NotIsolated(
                f"{harness}: credential {src} is absent, so an isolated "
                f"home cannot authenticate. Not falling back to the "
                f"operator's home.")
        # No exists-check: the destination was just proved empty, so a
        # name already there would mean something wrote into the home
        # between the two, and skipping it silently is the same hole in
        # miniature.
        link = dest / name
        link.parent.mkdir(parents=True, exist_ok=True)
        # ABSOLUTE. `symlink_to` with a relative source resolves it
        # against the LINK's directory, not the caller's, so a relative
        # `src` produced a link pointing inside the minimal home — a
        # dangling one, in a home that looked complete because an entry
        # named `auth.json` was there. Surfaced while reproducing the
        # empty-HOME finding above; `build_home` returned normally.
        link.symlink_to(src.resolve())
    return dest


def dispatch_env(harness, home=None, env=None):
    """The environment overrides that isolate `harness`.

    `home` is a directory from build_home. It is required for a
    home-mechanism harness and ignored for a flag-mechanism one.
    """
    env = os.environ if env is None else env
    spec = HARNESSES.get(harness)
    if spec is None:
        raise NotIsolated(f"{harness!r} has no isolation entry")
    if spec["mechanism"] == "flags":
        return {}
    # Same rule, one function along: `home=""` would emit
    # `CODEX_HOME=""`, which the harness reads as unset and answers by
    # loading the operator's real home. Found by looking for the shape
    # rather than the instance, after the instance was reported.
    if not home:
        raise NotIsolated(
            f"{harness} is isolated by relocating its home, and no home "
            f"was built (got {home!r}). Call build_home first.")
    out = {spec["home_env"]: str(home)}
    for extra in spec.get("also_env", ()):
        # HOME is redirected to the minimal home too, so that a harness
        # looking for a SIBLING vendor's dotfile -- grok reading
        # ~/.claude/CLAUDE.md -- finds nothing there either.
        out[extra] = str(home)
    return out


def dispatch_flags(harness):
    """The argv fragment that isolates `harness`, possibly empty."""
    spec = HARNESSES.get(harness)
    if spec is None:
        raise NotIsolated(f"{harness!r} has no isolation entry")
    return list(spec["flags"])


def isolated(harness, root, env=None):
    """Everything a launcher needs: (env_overrides, argv_fragment).

    The single entry point. A launcher that calls this cannot dispatch
    an unisolated harness, because there is no argument that turns the
    isolation off.
    """
    spec = HARNESSES.get(harness)
    if spec is None:
        raise NotIsolated(
            f"{harness!r} has no isolation entry: dispatching it would "
            f"carry the operator's personal setup into this project.")
    home = build_home(harness, root, env) if spec["mechanism"] == "home" else None
    return dispatch_env(harness, home, env), dispatch_flags(harness)


def report():
    """What is known about each harness, as JSON. For the record, and
    for a check that wants to notice an entry going stale."""
    # The WHOLE entry. It reported `mechanism` and `measured` only,
    # while promising "what is known about each harness" and naming its
    # own purpose as noticing an entry going stale -- and a check that
    # cannot see `flags` or `auth_files` cannot notice those going
    # stale, which is the operative half (PR #40 follow-up).
    return json.dumps(HARNESSES, indent=2, sort_keys=True)


def _main(argv=None):
    """A shell launcher needs the same answer this module already
    holds. Giving it one is what keeps the flags from being written
    down twice and drifting apart -- the duplicate copy is always the
    one that misses the next fix.

        eval "$(isolation.py --sh claude /tmp/home)"

    emits `ISO_FLAGS` and any environment assignments, and exits
    non-zero with an explanation on a harness that cannot be isolated,
    so a launcher that checks its exit status cannot dispatch one.
    """
    import argparse
    import shlex

    ap = argparse.ArgumentParser(description="isolation facts for a launcher")
    ap.add_argument("--sh", metavar="HARNESS",
                    help="emit shell assignments for this harness")
    ap.add_argument("root", nargs="?",
                    help="directory to build a minimal home in (--sh only)")
    args = ap.parse_args(argv)

    if not args.sh:
        print(report())
        return 0
    try:
        if HARNESSES.get(args.sh, {}).get("mechanism") == "home" and not args.root:
            raise NotIsolated(
                f"{args.sh} is isolated by relocating its home; pass a "
                f"directory to build one in")
        env, flags = isolated(args.sh, args.root or "")
    except NotIsolated as exc:
        print(f"echo {shlex.quote('REFUSED: ' + str(exc))} >&2; exit 78")
        return 78
    spec = HARNESSES[args.sh]
    sess = spec["sessions"]
    base = (str(Path(os.environ.get("HOME") or Path.home()))
            if sess["under"] == "real" else str(args.root))
    # Emitted as assignments, not exports: the launcher must apply these
    # to the HARNESS only. Exporting HOME would relocate the launcher's
    # own lookups too, and it still needs the real one.
    for k, v in sorted(env.items()):
        print(f"ISO_ENV_{k}={shlex.quote(v)}")
    print(f"ISO_ENV={shlex.quote(' '.join(f'{k}={v}' for k, v in sorted(env.items())))}")
    print(f"ISO_FLAGS={shlex.quote(' '.join(flags))}")
    print(f"ISO_STORE={shlex.quote(str(Path(base) / sess['path']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
