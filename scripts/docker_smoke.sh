#!/usr/bin/env bash
# Smoke test of the production image with docker-compose.yml: health and version, the four engines,
# one scan with each engine in both measures (they must agree), the non-root user, read-only
# folders and image, and the optional password.
#
#   docker build --build-arg IMAGE_TAG=ci -t drdonoso/disksprawl:ci .
#   IMAGE_TAG=ci scripts/docker_smoke.sh
set -euo pipefail

export IMAGE_TAG=${IMAGE_TAG:-ci}
export APP_PORT=${APP_PORT:-7780}
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-disksprawl-smoke}
base="http://localhost:$APP_PORT"
work=$(mktemp -d)
sample="$work/sample"
python3 scripts/make_sample_tree.py "$sample"
chmod -R a+rX "$work"
# The production Compose file, plus the sample tree as a folder to analyze.
cat > "$work/compose.override.yml" <<EOF
services:
  app:
    volumes:
      - $sample:/data/sample:ro
EOF
export COMPOSE_FILE="docker-compose.yml:$work/compose.override.yml"

cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then docker compose logs --no-color --tail=200 || true; fi
    docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$work"
    exit "$status"
}
trap cleanup EXIT

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
send() { curl -fsS -H "Origin: $base" -H 'X-DiskSprawl-Request: 1' -H 'Content-Type: application/json' "$@"; }

docker compose up -d --pull never --wait --wait-timeout 120
test "$(code "$base/health")" = 200
curl -fsS "$base/api/version" | jq -e --arg tag "$IMAGE_TAG" '.version == $tag' >/dev/null
curl -fsS "$base/api/engines" | jq -c '[.items[] | {id, available, version}]'
curl -fsS "$base/api/engines" | jq -e '[.items[] | select(.available and .version != null)] | length == 4' >/dev/null
test "$(docker compose exec -T app id -u)" = 1000
# Nothing can be written outside /app/data and /tmp: not the analyzed folders, not the image.
if docker compose exec -T app touch /data/sample/written 2>/dev/null; then echo 'The sample tree is writable' >&2; exit 1; fi
if docker compose exec -T app touch /app/written 2>/dev/null; then echo 'The image is writable' >&2; exit 1; fi

directory=$(send -X POST --data '{"name":"Sample","path":"/data/sample","pace":"max"}' "$base/api/directories" | jq -r .directory.id)
for measure in apparent disk; do
    results=()
    for engine in native du ncdu gdu; do
        body=$(jq -nc --arg engine "$engine" --arg measure "$measure" \
            '{name: "Sample", path: "/data/sample", engine: $engine, measure: $measure, pace: "max",
              threads: (if $engine == "du" then 1 else 4 end)}')
        send -X PUT --data "$body" "$base/api/directories/$directory" >/dev/null
        scan=$(send -X POST "$base/api/directories/$directory/scan" | jq -r .scan_id)
        for _ in $(seq 1 180); do
            state=$(curl -fsS "$base/api/scans/$scan" | jq -r .status)
            if [ "$state" != queued ] && [ "$state" != running ]; then break; fi
            sleep 1
        done
        result=$(curl -fsS "$base/api/scans/$scan")
        echo "$measure $engine: $(jq -c '{status, total_bytes, dir_count, file_count, error_count, duration_seconds}' <<<"$result")"
        jq -e '.status == "completed" and .error_count == 0' <<<"$result" >/dev/null
        results+=("$(jq -r '[.total_bytes, .dir_count, (if .engine == "du" then "-" else .file_count end)] | @tsv' <<<"$result")")
    done
    # The same totals from every engine (du counts no files: compared with "-").
    totals=$(printf '%s\n' "${results[@]}" | cut -f1,2 | sort -u | wc -l)
    files=$(printf '%s\n' "${results[@]}" | cut -f3 | grep -v '^-$' | sort -u | wc -l)
    if [ "$totals" -ne 1 ] || [ "$files" -ne 1 ]; then echo "The engines disagree ($measure)" >&2; exit 1; fi
done
curl -fsS "$base/api/directories/$directory/tree" | jq -e '.size > 0 and (.children | length > 0)' >/dev/null

# With a password, the API needs a session.
AUTH_PASSWORD=ci-password-not-a-secret docker compose up -d --pull never --wait --wait-timeout 120
test "$(code "$base/api/directories")" = 401
test "$(code "$base/health")" = 200
test "$(code -c "$work/cookies" -H "Origin: $base" -H 'X-DiskSprawl-Request: 1' -H 'Content-Type: application/json' \
    --data '{"password":"ci-password-not-a-secret"}' "$base/api/auth/login")" = 204
test "$(code -b "$work/cookies" "$base/api/directories")" = 200
curl -fsS "$base/" | grep -q '<div id="root"></div>'
echo "Smoke test passed"
