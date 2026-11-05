#!/bin/bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
task_dir=$(mktemp -d)
project="epoca-smoke-$$"
compose() { docker compose -p "$project" -f "$task_dir/compose.yaml" -f "$task_dir/override.yaml" "$@"; }
cleanup() {
    result=$?
    if [[ $result != 0 ]]; then compose logs --tail 80 epoca-api-blue || true; fi
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    docker network rm "$project" >/dev/null 2>&1 || true
    rm -rf "$task_dir"
    exit "$result"
}
trap cleanup EXIT
cp "$project_dir/epoca/compose.yaml" "$task_dir/compose.yaml"
printf 'networks:\n  portfolio:\n    name: %s\n' "$project" > "$task_dir/override.yaml"
printf '%s\n' 'POSTGRES_USER=epoca' 'POSTGRES_PASSWORD=disposable-runtime-test' 'POSTGRES_DB=epoca' > "$task_dir/database.env"
printf '%s\n' 'DB_URL=jdbc:postgresql://postgres:5432/epoca' 'DB_USER=epoca' 'DB_PASSWORD=disposable-runtime-test' 'JWT_SECRET=disposable-runtime-verification-key-only' > "$task_dir/api.env"
printf '%s\n' 'DATABASE_URL=postgres://epoca:disposable-runtime-test@postgres:5432/epoca' > "$task_dir/gateway.env"
export API_BLUE_IMAGE=ghcr.io/bartkozik/epoca-backend@sha256:722468e37b0daa408895c7e47384be5c143f8ff665f5adc2f42fa10c821d5e4d
export API_GREEN_IMAGE=$API_BLUE_IMAGE
export GATEWAY_BLUE_IMAGE=ghcr.io/bartkozik/epoca-demo-gateway:check
export GATEWAY_GREEN_IMAGE=$GATEWAY_BLUE_IMAGE
export WEB_BLUE_IMAGE=ghcr.io/bartkozik/epoca-frontend:check
export WEB_GREEN_IMAGE=$WEB_BLUE_IMAGE
docker network create "$project" >/dev/null
compose up -d --wait --wait-timeout 90 postgres
compose run --rm --no-deps -T epoca-api-blue pl.iterators.epoca.main.MigrateMain
compose up -d --no-deps --wait --wait-timeout 120 epoca-api-blue
compose exec -T epoca-api-blue curl --fail --silent http://127.0.0.1:9000/api/v1/health-check
