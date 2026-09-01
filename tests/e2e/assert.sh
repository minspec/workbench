#!/usr/bin/env bash

if [[ $# -ne 1 || ! -d "$1" || ! -f "$1/bin/console" || ! -f "$1/config/bundles.php" ]]; then
    printf '%s\n' 'app: expected <app-dir> to be a Symfony application; found missing or invalid directory; needed pass a Symfony app directory containing bin/console and config/bundles.php' >&2
    exit 2
fi

app_dir=$1
failures=0

one_line() {
    printf '%s' "$1" | tr '\r\n' '  ' | tr -s ' '
}

bundles_file="$app_dir/config/bundles.php"
if grep -Eq "Minspec\\\\FixtureHello\\\\FixtureHelloBundle::class[[:space:]]*=>[[:space:]]*\\[[[:space:]]*['\"]all['\"][[:space:]]*=>[[:space:]]*true[[:space:]]*\\]" "$bundles_file"; then
    printf '%s\n' 'PASS: FixtureHelloBundle is registered for all environments'
else
    printf '%s\n' 'bundle: expected FixtureHelloBundle registered for all environments; found no matching all-environments registration'
    failures=$((failures + 1))
fi

yaml_file="$app_dir/config/packages/fixture_hello.yaml"
if [[ ! -f "$yaml_file" ]]; then
    printf '%s\n' 'recipe config: expected config/packages/fixture_hello.yaml with fixture_hello.greeting wired-by-recipe; found file missing'
    failures=$((failures + 1))
elif grep -Eq "^[[:space:]]*fixture_hello\\.greeting:[[:space:]]*(['\"])?wired-by-recipe\\1[[:space:]]*(#.*)?$" "$yaml_file"; then
    printf '%s\n' 'PASS: recipe configuration defines fixture_hello.greeting as wired-by-recipe'
else
    printf '%s\n' 'recipe config: expected fixture_hello.greeting value wired-by-recipe; found parameter line missing or different'
    failures=$((failures + 1))
fi

container_result=$(cd "$app_dir" && php bin/console debug:container fixture_hello.service 2>&1)
container_status=$?
if [[ $container_status -eq 0 ]]; then
    printf '%s\n' 'PASS: public container service fixture_hello.service exists'
else
    printf 'service: expected public container service fixture_hello.service; found command exit %d: %s\n' \
        "$container_status" "$(one_line "$container_result")"
    failures=$((failures + 1))
fi

command_capture=$(
    cd "$app_dir" || exit 125
    php bin/console fixture:hello 2>&1
    command_status=$?
    printf '\036%d' "$command_status"
)
command_status=${command_capture##*$'\036'}
command_output=${command_capture%$'\036'*}
expected_output=$'fixture-hello: wired-by-recipe\n'

if [[ $command_status -eq 0 && "$command_output" == "$expected_output" ]]; then
    printf '%s\n' 'PASS: fixture:hello prints exactly fixture-hello: wired-by-recipe'
else
    printf 'command: expected exact output fixture-hello: wired-by-recipe; found exit %s, output <%s>\n' \
        "$command_status" "$(one_line "$command_output")"
    failures=$((failures + 1))
fi

if [[ $failures -ne 0 ]]; then
    exit 1
fi

exit 0
