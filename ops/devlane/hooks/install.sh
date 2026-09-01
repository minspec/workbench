#!/bin/sh
# install.sh -- install the crossing-recorder hooks (ops/devlane/hooks)
# into the clone's shared hooks dir, so every worktree fires them.
#
#   sh ops/devlane/hooks/install.sh [--force]
#
# Idempotent: a re-run over identical hooks is a quiet success. An
# existing DIFFERENT hook of the same name is never clobbered -- the
# install refuses, naming the file; --force replaces it.

set -u

force=0
case "${1:-}" in
    --force) force=1 ;;
    "") ;;
    *)
        echo "usage: install.sh [--force]" >&2
        exit 2
        ;;
esac

# The hooks to install live beside this script; fall back to the
# repo-relative package path when run from the repo root.
src_dir=$(dirname "$0")
if [ ! -f "$src_dir/post-checkout" ] || [ ! -f "$src_dir/post-commit" ] || [ ! -f "$src_dir/commit-msg" ]; then
    src_dir="ops/devlane/hooks"
fi
if [ ! -f "$src_dir/post-checkout" ] || [ ! -f "$src_dir/post-commit" ] || [ ! -f "$src_dir/commit-msg" ]; then
    echo "install.sh: cannot find post-checkout/post-commit/commit-msg beside $0 or under ops/devlane/hooks" >&2
    exit 1
fi

common=$(git rev-parse --git-common-dir) || exit 1
# Detect by exit status, not value: an explicitly EMPTY hooksPath is
# still configured, and git will not run hooks from $common/hooks.
if configured=$(git config --get core.hooksPath); then
    # Installing into $common/hooks would report success while git runs
    # hooks from the configured path — a successful inert install.
    echo "install.sh: core.hooksPath is configured (${configured:-empty}); git will not run hooks from $common/hooks — install into that path yourself or unset core.hooksPath" >&2
    exit 1
fi
hooks_dir="$common/hooks"
mkdir -p "$hooks_dir" || exit 1

# Refuse before copying anything: report every conflict, touch nothing.
status=0
for name in post-checkout post-commit commit-msg; do
    dest="$hooks_dir/$name"
    if { [ -e "$dest" ] || [ -h "$dest" ]; } && [ "$force" -eq 0 ] && ! cmp -s "$src_dir/$name" "$dest"; then
        echo "install.sh: existing $name differs from the packaged hook; not clobbering (use --force)"
        echo "install.sh: existing $name differs from the packaged hook; not clobbering (use --force)" >&2
        status=1
    fi
done
[ "$status" -eq 0 ] || exit "$status"

for name in post-checkout post-commit commit-msg; do
    dest="$hooks_dir/$name"
    if [ -f "$dest" ] && [ ! -h "$dest" ] && cmp -s "$src_dir/$name" "$dest"; then
        chmod 755 "$dest" || exit 1
        continue
    fi
    # Unlink first: cp onto a symlink writes THROUGH it to a target the
    # installer was never pointed at. The entry must be a fresh regular file.
    rm -f "$dest" || exit 1
    cp "$src_dir/$name" "$dest" || exit 1
    chmod 755 "$dest" || exit 1
done
exit 0
