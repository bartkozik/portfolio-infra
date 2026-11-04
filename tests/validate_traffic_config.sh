#!/bin/bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
task_dir=$(mktemp -d)
trap 'rm -rf "$task_dir"' EXIT
cp "$project_dir/traffic/compose.yaml" "$task_dir/compose.yaml"
printf '%s\n' 'POSTGRES_USER=ptm' 'POSTGRES_PASSWORD=test-only' 'POSTGRES_DB=traffic' > "$task_dir/database.env"
printf '%s\n' 'PTM_DATABASE_URL=postgres://ptm:test-only@postgres:5432/traffic' > "$task_dir/api.env"
export API_BLUE_IMAGE=ghcr.io/bartkozik/pm-traffic-manager-api:check
export API_GREEN_IMAGE=$API_BLUE_IMAGE
export WEB_BLUE_IMAGE=ghcr.io/bartkozik/pm-traffic-manager-web:check
export WEB_GREEN_IMAGE=$WEB_BLUE_IMAGE
docker compose -f "$task_dir/compose.yaml" config --quiet
mkdir -p "$task_dir/caddy/sites"
python3 - "$project_dir" "$task_dir" <<'PY'
import importlib.util
from pathlib import Path
import sys
repo, temp = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location('traffic', repo / 'bin' / 'traffic_deploy.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
digest = 'sha256:' + 'a' * 64
(temp / 'caddy' / 'Caddyfile').write_text(module.deploy.configuration('blue', digest))
(temp / 'caddy' / 'sites' / 'traffic.caddy').write_text(module.configuration('green', digest))
PY
docker run --rm -v "$task_dir/caddy:/etc/caddy:ro" --entrypoint caddy ghcr.io/bartkozik/portfolio-infra@sha256:1a23c66865a1e81113270900934e615f34bd8a05be409a6a7ddd5272d46bd35d validate --config /etc/caddy/Caddyfile --adapter caddyfile
