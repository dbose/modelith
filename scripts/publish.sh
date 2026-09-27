#!/usr/bin/env bash
# Build, publish modelith-dbt to PyPI, then commit and tag the release.
# Usage: ./scripts/publish.sh --version 0.3.3
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    *) echo "usage: $0 --version X.Y.Z" >&2; exit 2 ;;
  esac
done
[ -n "$VERSION" ] || { echo "usage: $0 --version X.Y.Z" >&2; exit 2; }

echo "==> releasing modelith-dbt $VERSION"
sed -i '' -E "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml

# The canvas bundle is a committed build output; uv build does not run vite.
# vite needs Node 18+ (default node 16 fails on crypto.getRandomValues).
export PATH="$HOME/.nvm/versions/node/v20.20.1/bin:$PATH"
(cd canvas && npx vite build >/dev/null)

uv cache clean modelith-dbt >/dev/null 2>&1 || true
rm -rf dist
uv build

read -rsp "PyPI token: " TOKEN < /dev/tty; echo
UV_PUBLISH_USERNAME=__token__ UV_PUBLISH_PASSWORD="$TOKEN" uv publish dist/*

# Only tag once the upload succeeded, so a failed publish leaves no tag behind.
echo "==> committing and tagging v$VERSION"
git add pyproject.toml uv.lock packages/server/src/mdl_server/static

# Commit the version bump ONLY if it isn't already committed. When the bump landed
# via a merged PR (the common case), the tree is clean here and `git commit` would
# abort the whole script under `set -e`, skipping the tag/push below — the exact bug
# that left v0.6.10 published but untagged. `git diff --cached --quiet` is 0 when there
# is nothing staged, so we commit only when there IS.
if git diff --cached --quiet; then
  echo "    version $VERSION already committed; tagging the current commit"
else
  git commit -m "Release $VERSION"
fi

# Tag the commit that actually carries this version (HEAD now does, either way).
# Skip if the tag already exists (a re-run after a partial publish) so it never aborts.
if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
  echo "    tag v$VERSION already exists; leaving it in place"
else
  git tag "v$VERSION"
fi

# Push the current branch and the tag. Pushing the tag is what release consumers rely
# on, so do it even when the branch push is a no-op (already up to date).
git push
git push origin "v$VERSION"
