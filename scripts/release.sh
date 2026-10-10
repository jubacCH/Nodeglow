#!/bin/sh
# Cut a release locally: bump VERSION, move the Unreleased changelog section
# to the new version, commit and create an annotated tag. Nothing is pushed.
#
#   scripts/release.sh 1.2.0          # stable release
#   scripts/release.sh 1.3.0-rc.1     # pre-release (no "latest"/X.Y image tags)
#
# Then review and publish:
#   git show --stat HEAD
#   git push origin main && git push origin v1.2.0   # triggers release.yml
#
# POSIX sh; keep it shellcheck-clean.

set -eu

die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }

[ $# -eq 1 ] || die "usage: scripts/release.sh X.Y.Z[-pre]"
VERSION=${1#v}
TAG="v$VERSION"
printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$' \
    || die "not a semantic version: $1"

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
CHANGELOG="CHANGELOG.md"
[ -f "$CHANGELOG" ] || die "$CHANGELOG not found"
[ -f VERSION ] || die "VERSION not found"

[ -z "$(git status --porcelain)" ] || die "working tree is not clean"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    die "tag $TAG already exists"
fi
branch=$(git symbolic-ref -q --short HEAD || echo "")
if [ "$branch" != "main" ]; then
    printf 'warning: releasing from %s, not main\n' "${branch:-a detached HEAD}" >&2
fi

current=$(sed -n '/^[[:space:]]*[^#[:space:]]/{p;q;}' VERSION | tr -d '[:space:]')
info "VERSION $current -> $VERSION"

# The Unreleased section must say something: an empty release note is almost
# always a forgotten changelog entry.
unreleased=$(awk '
    /^## \[Unreleased\]/ { on = 1; next }
    on && /^## \[/ { exit }
    on && /[^[:space:]]/ { print }
' "$CHANGELOG")
[ -n "$unreleased" ] || die "the [Unreleased] section of $CHANGELOG is empty — describe the release first"

today=$(date -u +%Y-%m-%d)
repo_url="https://github.com/jubacCH/Nodeglow"
prev_tag=$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || echo "")

tmp="$CHANGELOG.tmp.$$"
awk -v ver="$VERSION" -v day="$today" -v url="$repo_url" -v prev="$prev_tag" -v tag="$TAG" '
    BEGIN { done = 0; links = 0 }
    /^## \[Unreleased\]/ && !done {
        print "## [Unreleased]"
        print ""
        print "## [" ver "] - " day
        done = 1
        next
    }
    /^\[Unreleased\]:/ {
        print "[Unreleased]: " url "/compare/" tag "...HEAD"
        if (prev != "") print "[" ver "]: " url "/compare/" prev "..." tag
        else print "[" ver "]: " url "/releases/tag/" tag
        links = 1
        next
    }
    { print }
    END {
        if (!links) {
            print ""
            print "[Unreleased]: " url "/compare/" tag "...HEAD"
            if (prev != "") print "[" ver "]: " url "/compare/" prev "..." tag
            else print "[" ver "]: " url "/releases/tag/" tag
        }
    }
' "$CHANGELOG" > "$tmp"
mv "$tmp" "$CHANGELOG"

printf '%s\n# %s\n' "$VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > VERSION

git add VERSION "$CHANGELOG"
git commit -q -m "release: $TAG"
git tag -a "$TAG" -m "Nodeglow $VERSION"

info "committed and tagged $TAG (not pushed)"
cat <<EOF

Review:   git show --stat HEAD
Publish:  git push origin ${branch:-HEAD} && git push origin $TAG
Undo:     git tag -d $TAG && git reset --hard HEAD~1
EOF
