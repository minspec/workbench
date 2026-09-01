#!/usr/bin/env python3
"""One answer to "what commands does this shell text run, and where do they start?".

Three hooks in this directory each decide something about a Bash payload — is it
consequential, did it move the ground, must it be refused — and each of them first has to
split the text into pieces and find the program name in each piece. Three copies of that
split existed, they disagreed, and every disagreement was a defect in whichever copy lost:

  * `git -C . push` was invisible to two of them and visible to the third.
  * `npm test` was invisible to the rule written for it, because `npm` was on a runner list
    and got peeled off before the rule was tried.
  * `echo "done; git checkout main"` fired the boundary hook, because the split ignored
    quoting — it fired on this file's own test probe while this file was being written.
  * A `git push` written inside a heredoc BODY — a commit message, a note, a memory file —
    fired two of the three. Only one blanked data heredocs.

So this module owns the shape and nothing else. It has no opinion about what any command
MEANS: no list of dangerous programs, no notion of consequence, no rules. Those stay with
the hook that has the reason for them. What it owes its callers is that a command the
caller can recognise in its plainest form stays recognisable when it arrives wrapped —
behind `sudo`, inside `for … do … done`, after `git -C dir`, split over a line
continuation — and that text which merely SAYS a command, inside quotes or a data heredoc,
is not offered as one.

Two views, because the callers need different things and the difference is real:

  statements(text)  splits on statement separators only, keeping a PIPELINE whole.
                    `pytest -q 2>&1 | tail -4` is one statement. A rule that reads "this
                    runner, clipped by that head" needs both halves in one string.

  commands(text)    every position where a program name can appear: pipeline components and
                    command substitutions too, each one peeled down to its program name.
                    A rule that reads "^git push" needs the `git` at the front.

`commands()` returns VARIANTS, not a single rewriting. `sudo -u ci git push` yields both
`ci git push` and `git push`, because guessing which options take an operand means keeping
a list of options — the same "subset of an unenumerated set" mistake the hooks next door
exist to catch. Offering both costs one more regex match and needs no list. Callers match
patterns anchored at `^`, so the extra variants match nothing.

The corpus lives in `ops/devlane/hooks/tests/`.
"""

import re

MAX_VARIANTS = 400          # a bound, not a tuning knob: runaway text stays cheap

# ---------------------------------------------------------------- program-name vocabulary

# Wrappers: the program name that matters is the NEXT one, always. `sudo`, `env` and
# `timeout` are here because they run something else, never because of what they are.
RUNNERS = frozenset((
    "sudo", "doas", "command", "exec", "builtin", "nohup", "setsid", "stdbuf",
    "nice", "ionice", "chrt", "time", "timeout", "xargs", "env",
    "npx", "uvx", "poe",
))

# Launchers that wrap a command ONLY in their `<name> run …` form. `npm` is the reason this
# distinction exists: as a bare runner it swallowed `npm test` (killing the rule written for
# it) and `npm publish` (killing the rule written for THAT), while `npm run build` genuinely
# needs peeling. `run` is the whole difference and it is written in the command.
RUN_LAUNCHERS = frozenset(("uv", "poetry", "pipenv", "npm", "yarn", "pnpm", "bun", "rye", "pdm"))

# Shell grammar that can sit in front of a command. Peeled, never matched against.
KEYWORDS = frozenset((
    "if", "then", "elif", "else", "fi", "do", "done", "while", "until",
    "for", "case", "esac", "select", "function", "coproc", "!", "{", "}", "(", ")",
))

# A heredoc whose body is fed to one of these EXECUTES; anything else is data.
INTERPRETERS = frozenset((
    "sh", "bash", "zsh", "ksh", "dash", "ash", "fish",
    "python", "python2", "python3", "perl", "ruby", "node", "deno",
    "awk", "gawk", "mawk", "php", "tclsh", "osascript", "Rscript",
))
_VERSIONED = re.compile(r"^(python|perl|ruby|php|node)[\d.]+$")

# Programs whose `-c` argument is a whole shell command in a string.
SHELLS = frozenset(("sh", "bash", "zsh", "ksh", "dash", "ash"))

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_DURATION = re.compile(r"^\d+(?:\.\d+)?[smhdSMHD]?$")     # `timeout 60`, `timeout 1.5s`

# The delimiter word must be a plain word, and the body ends on a line that is only the
# delimiter. `[^\n]*` after it is what makes `cat <<'EOF' | python3` a matchable head — the
# pipe that decides whether the body executes lives AFTER the delimiter.
HEREDOC = re.compile(
    r"<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1[^\n]*\n(?:.*?\n)??[ \t]*\2[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def is_interpreter(word: str) -> bool:
    """Is this bare word the name of something that runs its stdin as code?"""
    if not word:
        return False
    base = word.rsplit("/", 1)[-1]
    return base in INTERPRETERS or bool(_VERSIONED.match(base))


# ------------------------------------------------------------------------------ scanning

def _scan(text: str, split_pipes: bool):
    """Split shell text, honouring quotes.

    Separators inside quotes are text: `git commit -m "wip; git push next"` is ONE command,
    and splitting it at the `;` made the precheck hook deny a commit for what its message
    said. Command substitution still opens inside double quotes, because it still runs:
    `"$(git push)"` is a push.

    Returns None when the text ends inside an open quote — the caller falls back to a
    quote-blind split rather than swallowing everything after a stray apostrophe.
    """
    segs, buf, quote, nest = [], [], None, []
    i, n = 0, len(text)

    def flush():
        segs.append("".join(buf))
        buf.clear()

    while i < n:
        ch = text[i]
        if quote == "'":                              # single quotes: nothing is special
            if ch == "'":
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:                  # escaped char: never a separator
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        # A substitution opens a command position even inside double quotes, because it
        # still runs. The quote state is stacked and restored at the closing paren.
        if split_pipes and text.startswith("$(", i):
            flush()
            nest.append(quote)
            quote = None
            i += 2
            continue
        if split_pipes and ch == "`":
            flush()
            i += 1
            continue
        if (split_pipes and ch == "(" and quote is None
                and (i == 0 or text[i - 1] in " \t\n;&|(")):      # a subshell, not `f()`
            flush()
            nest.append(quote)
            i += 1
            continue
        if split_pipes and ch == ")" and nest:
            flush()
            quote = nest.pop()
            i += 1
            continue
        if quote == '"':
            if ch == '"':
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch in "\n;":
            flush()
            i += 1
            continue
        if ch == "|":
            two = text.startswith("||", i)
            if two or split_pipes:
                flush()
                i += 2 if two else 1
                continue
        if ch == "&":
            if text.startswith("&&", i):
                flush()
                i += 2
                continue
            # `2>&1` and `&>log` are redirections, not the background operator. Splitting
            # there tore `playwright test … 2>&1 | tail -4` in half and lost the rule.
            if not (i and text[i - 1] == ">") and not text.startswith("&>", i):
                flush()
            else:
                buf.append(ch)
            i += 1
            continue
        buf.append(ch)
        i += 1
    segs.append("".join(buf))
    if quote is not None:
        return None
    return segs


_BLIND_STATEMENTS = re.compile(r";|&&|\|\||\n")
_BLIND_COMMANDS = re.compile(r"[;&|]{1,2}|\$\(|`|\)|\n")


def _split(text: str, split_pipes: bool):
    segs = _scan(text, split_pipes)
    if segs is None:                       # unbalanced quoting: fall back, do not swallow
        pattern = _BLIND_COMMANDS if split_pipes else _BLIND_STATEMENTS
        segs = pattern.split(text)
    return [s.strip() for s in segs if s.strip()]


# ----------------------------------------------------------------------------- heredocs

def _head_line(text: str, at: int) -> str:
    """The line carrying a `<<` operator, with the operator itself removed.

    Both sides matter. `cat > x.sh <<'EOF'` is `cat` writing a file, whatever the file is
    called; `cat <<'EOF' | python3` is python running the body, and the `python3` is on the
    far side of the operator.
    """
    start = text.rfind("\n", 0, at) + 1
    end = text.find("\n", at)
    if end == -1:
        end = len(text)
    before = text[start:at]
    after = text[at:end]
    after = after.split(None, 1)[1] if len(after.split(None, 1)) > 1 else ""
    return before + " " + after


def _head_executes(head: str) -> bool:
    """Does the body of a heredoc opened by this head get RUN?

    Two tests, and the second one is the fix for a real refusal. The command word settles
    `python3 - <<'PY'` and `cat > x.sh <<'EOF'`. A bare interpreter TOKEN anywhere in the
    head settles `ssh host bash <<'EOF'`, where the interpreter is an argument.

    What is deliberately gone is the substring test this replaced: `\\bsh\\b` matched the
    `sh` in `x.sh`, so `cat > setup.sh <<'EOF'` and `tee notes.sh <<'EOF'` were read as
    executing their bodies. That refused a memory-file write, in this repo, because the
    file was named `…-bash.md`.
    """
    for c in commands(head, _heredocs=False):
        if is_interpreter(_word(c)):
            return True
    return any(is_interpreter(t.strip("()[]{}'\"`;&<>")) for t in head.split())


def strip_data_heredocs(text: str) -> str:
    """Blank heredoc bodies that are DATA, keep the ones that are CODE.

    A commit message explaining why an unguarded `.replace()` is dangerous contains every
    token of the thing it warns about. A note listing `git push` mentions a push. Neither
    runs anything, and both were treated as if they did.

    Idempotent: the body and its terminator go, the operator stays, and a second pass finds
    no body to take.
    """
    if "<<" not in text:
        return text

    def repl(m):
        if _head_executes(_head_line(text, m.start())):
            return m.group(0)
        whole = m.group(0)
        cut = whole.find("\n")
        return whole if cut == -1 else whole[:cut]

    return HEREDOC.sub(repl, text)


_CONTINUATION = re.compile(r"[ \t]*\\\n[ \t]*")


def _join_continuations(text: str) -> str:
    """`git \\<newline> push` is one command. Splitting on the newline made it two."""
    return _CONTINUATION.sub(" ", text)


def _prepare(text: str, heredocs: bool = True) -> str:
    text = text or ""
    if heredocs:
        text = strip_data_heredocs(text)
    return _join_continuations(text)


# -------------------------------------------------------------------------- normalisation

def _first_token(s: str):
    """The first shell word, and the rest of the string, with quoted spans kept whole.

    `GIT_SSH_COMMAND='ssh -i k' git push` is one assignment followed by a command. Splitting
    it on whitespace made it three words, the first of which still looked like an
    assignment — so the assignment was peeled, `-i` became the program, and the push
    vanished. Found by the corpus row for it, not by reading this function.
    """
    i, n, quote = 0, len(s), None
    while i < n and s[i] in " \t":
        i += 1
    start = i
    while i < n:
        ch = s[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in " \t":
            break
        elif ch == "\\" and i + 1 < n:
            i += 1
        i += 1
    return s[start:i], s[i:].lstrip()


def _tokens(s: str):
    out = []
    while s:
        tok, s = _first_token(s)
        if not tok:
            break
        out.append(tok)
    return out


def tokens(cmd: str):
    """The shell words of one command position, quoted spans kept whole.

    Public because a caller that needs to read a command's OPTIONS — which
    repository `git -C other -c x=y push` acts on — otherwise writes its own
    splitter, and a fourth copy of the split is what this module exists to
    prevent. It answers where the words are, never what they mean.
    """
    return _tokens(cmd)


def _word(cmd: str) -> str:
    return _first_token(cmd)[0]


def command_word(cmd: str) -> str:
    """The program name a command position invokes, once it is peeled."""
    for c in commands(cmd):
        return _word(c)
    return ""


def _peel_front(seg: str) -> str:
    """Remove one leading thing that is not a program name. Returns seg unchanged if none."""
    s = seg.lstrip()
    if not s:
        return s
    if s[0] in "({}!" and (len(s) == 1 or s[1].isspace() or s[0] in "({"):
        return s[1:]
    if s.startswith("\\") and len(s) > 1 and not s[1].isspace():
        return s[1:]                       # `\git` — quoting the name to skip an alias
    head, rest = _first_token(s)
    if head in KEYWORDS:
        return rest
    if _ASSIGNMENT.match(head):            # `VAR=1 git push`, and lowercase names too
        return rest
    return s


def _option_region(tail: list[str], prefix: str) -> list[str]:
    """Candidates for "the command starts somewhere after these options".

    Options that take an operand are not enumerated. The first non-option token is one
    candidate; if the option before it could have taken it, the token after is another. A
    surplus candidate matches no anchored pattern and costs one regex.
    """
    i = 0
    while i < len(tail) and tail[i].startswith("-"):
        i += 1
    if i >= len(tail):
        return []
    out = [" ".join(filter(None, [prefix, *tail[i:]]))]
    if i and "=" not in tail[i - 1]:
        out.append(" ".join(filter(None, [prefix, *tail[i + 1:]])))
    elif not i and _DURATION.match(tail[0]):          # `timeout 60 git push`
        out.append(" ".join(filter(None, [prefix, *tail[1:]])))
    return out


def _quoted_arg_after(cmd: str, flag: str) -> str:
    """The single argument following `flag`, unquoted — `bash -c 'git push'`."""
    m = re.search(rf"(?:^|\s){re.escape(flag)}\s+('([^']*)'|\"((?:[^\"\\]|\\.)*)\"|(\S+))", cmd)
    if not m:
        return ""
    return m.group(2) or m.group(3) or m.group(4) or ""


def _variants(seg: str, depth: int = 0):
    """Every plausible reading of one command position, peeled down to a program name."""
    out, seen, work = [], set(), [seg]
    while work and len(out) < MAX_VARIANTS:
        s = work.pop(0).strip()
        if not s or s in seen:
            continue
        seen.add(s)

        peeled = _peel_front(s)
        if peeled != s:
            work.append(peeled)
            continue

        toks = _tokens(s)
        w = toks[0]

        if w in RUNNERS:
            work.extend(_option_region(toks[1:], ""))
            continue                        # a wrapper is never the command
        if w in RUN_LAUNCHERS and len(toks) > 1 and toks[1] == "run":
            work.append(" ".join(toks[2:]))
            continue

        out.append(s)

        if w in ("git", "gh") and len(toks) > 1 and toks[1].startswith("-"):
            work.extend(_option_region(toks[1:], w))
        if depth < 2 and w in SHELLS and "-c" in toks[1:3]:
            inner = _quoted_arg_after(s, "-c")
            if inner:
                for sub in _split(_prepare(inner), split_pipes=True):
                    work.extend(_variants(sub, depth + 1))
    return out


# ------------------------------------------------------------------------------- the API

def statements(text: str, _heredocs: bool = True):
    """Statement-level pieces, PIPELINES KEPT WHOLE.

    For rules that read a pipeline as one thing: "this runner, clipped by that head".
    """
    return _split(_prepare(text, _heredocs), split_pipes=False)


def commands(text: str, _heredocs: bool = True):
    """Every position a program name can appear in, peeled to that name.

    Pipeline components and command substitutions included; runner prefixes, shell
    keywords, environment assignments and git/gh global options peeled off. The segment as
    written is always among the results, so nothing a caller recognises today stops being
    recognised.
    """
    out = []
    for seg in _split(_prepare(text, _heredocs), split_pipes=True):
        for v in _variants(seg):
            if v not in out:
                out.append(v)
        if len(out) >= MAX_VARIANTS:
            break
    return out


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        print("corpus moved to ops/devlane/hooks/tests/", file=sys.stderr)
        sys.exit(2)
    print(__doc__)
