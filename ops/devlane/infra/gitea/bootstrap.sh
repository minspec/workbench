#!/bin/sh
set -eu

until curl -fsS http://127.0.0.1:3000/api/healthz >/dev/null; do
    sleep 1
done

user=minspec
password_file=/var/lib/gitea/agent-lab/bootstrap-password
mkdir -p "${password_file%/*}"
if [ ! -s "$password_file" ]; then
    umask 077
    od -An -N24 -tx1 /dev/urandom | tr -d ' \n' > "$password_file"
fi
password=$(cat "$password_file")
gitea_cli() {
    env GITEA_WORK_DIR=/var/lib/gitea gitea \
        --config /etc/gitea/app.ini "$@"
}

if ! gitea_cli admin user list --admin | awk 'NR > 1 { print $2 }' \
    | grep -Fxq "$user"; then
    gitea_cli admin user create \
        --admin --username minspec --password "$password" \
        --email agent-lab@invalid.example --must-change-password=false
fi

state_dir=/var/lib/gitea/agent-lab
mkdir -p "$state_dir" /run/agent-lab
umask 077
if [ ! -f /var/lib/gitea/agent-lab/api-token ]; then
    gitea_cli admin user generate-access-token --username "$user" \
        --token-name agent-lab-bootstrap \
        --scopes read:repository,read:admin,read:user \
        --raw > /var/lib/gitea/agent-lab/api-token
fi
api_token=$(cat /var/lib/gitea/agent-lab/api-token)

create_repo() {
    case "$1" in --name) ;; *) return 64 ;; esac
    repo_name=$2
    if curl -fsS -H "Authorization: token $api_token" \
        "http://127.0.0.1:3000/api/v1/repos/$user/$repo_name" >/dev/null; then
        return
    fi
    curl -fsS --user "$user:$password" \
        -H 'Content-Type: application/json' \
        -d "{\"name\":\"$repo_name\",\"default_branch\":\"dev\"}" \
        http://127.0.0.1:3000/api/v1/user/repos >/dev/null
}
create_repo --name minspec

if [ ! -s /run/runner-token/token ]; then
    token=$(gitea_cli actions generate-runner-token)
    umask 077
    printf '%s\n' "$token" > /run/runner-token/token
fi

git config --global --add safe.directory /workspace
source_tree=/workspace
seed=
if ! git -C "$source_tree" rev-parse --verify HEAD >/dev/null 2>&1; then
    seed=$(mktemp -d)
    trap 'rm -rf "$seed"' EXIT INT TERM
    tar -C /workspace --exclude=.git -cf - . | tar -C "$seed" -xf -
    git -C "$seed" init --initial-branch=dev
    git -C "$seed" config user.name xormania
    git -C "$seed" config user.email 127287135+xormania@users.noreply.github.com
    git -C "$seed" add --all
    git -C "$seed" commit -m 'Seed dev tree'
    source_tree=$seed
fi
git -C "$source_tree" push --force \
    "http://$user:$password@127.0.0.1:3000/$user/minspec.git" \
    HEAD:refs/heads/dev
