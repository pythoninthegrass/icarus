#!/usr/bin/env bash
# Fetch Dokploy OpenAPI spec from GitHub for a given release tag.
# Usage: ./scripts/fetch_openapi.sh [tag] (e.g., v0.28.4)
# Omit tag to fetch the latest release.
set -euo pipefail

TAG="${1:-$(gh api repos/Dokploy/dokploy/releases/latest --jq '.tag_name')}"
VERSION="${TAG#v}"
OUTDIR="schemas/src"
OUTFILE="${OUTDIR}/openapi_${VERSION}.json"
KEEP_PRETTY=2

minify_old_specs() {
  local all total n specs
  all=$(ls "$OUTDIR"/openapi_*.json 2>/dev/null | sort -V)
  [ -z "$all" ] && return 0
  total=$(echo "$all" | wc -l | tr -d ' ')
  n=$((total - KEEP_PRETTY))
  [ "$n" -le 0 ] && return 0
  specs=$(echo "$all" | head -n "$n")
  while IFS= read -r f; do
    # already minified (jq -c writes a single line) -> skip for idempotency
    if [ "$(wc -l < "$f")" -gt 1 ]; then
      jq -c . "$f" > "$f.tmp" && mv "$f.tmp" "$f"
      echo "Minified: $f"
    fi
  done <<< "$specs"
}

mkdir -p "$OUTDIR"

DOWNLOAD_URL=$(gh api "repos/Dokploy/dokploy/contents/openapi.json?ref=${TAG}" --jq '.download_url')
curl -fsSL "$DOWNLOAD_URL" | jq . > "$OUTFILE"

if [ ! -s "$OUTFILE" ]; then
  echo "Error: $OUTFILE is empty — fetch failed for ${TAG}" >&2
  rm -f "$OUTFILE"
  exit 1
fi

echo "Saved: $OUTFILE ($(wc -c < "$OUTFILE") bytes)"

minify_old_specs
