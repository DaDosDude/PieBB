#!/usr/bin/env bash
# Update PieBB to the latest release, or run a test build.
#
#   ./update.sh                 switch to the newest release (vX.Y.Z tag), rebuild and start
#   ./update.sh --test FILE     unpack a PieBB .tar.gz over this copy, rebuild and start (nothing committed);
#                               the next plain ./update.sh discards it and returns to the latest release
#   ./update.sh --force         rebuild even when already on the latest release
#
# Your .env and everything in the piebb-data volume (settings, themes, cache) are never touched.
# Run it from a git clone of PieBB: git clone https://github.com/DaDosDude/PieBB.git
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# git checkout may replace this very file while it runs: carry on from a copy
if [ -z "${PIEBB_UPDATE_COPY:-}" ]; then
	copy="$(mktemp)"
	cp "$0" "$copy"
	PIEBB_UPDATE_COPY="$copy" PIEBB_DIR="$DIR" exec bash "$copy" "$@"
fi
trap 'rm -f "$PIEBB_UPDATE_COPY"' EXIT
trap 'echo "error: update.sh stopped unexpectedly (line $LINENO); nothing after that step was done" >&2' ERR
DIR="$PIEBB_DIR"
cd "$DIR"

COMPOSE="${COMPOSE:-docker compose}"         # override for testing
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8060/robots.txt}"
WAIT="${WAIT:-60}"                        # seconds to wait for PieBB to answer

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }

usage() { sed -n '2,10p' "$DIR/update.sh" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

healthy() {
	local i
	for ((i = 0; i < WAIT; i += 2)); do
		if curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL" 2>/dev/null; then
			return 0
		fi
		sleep 2
	done
	return 1
}

build() {
	say "Building and starting"
	$COMPOSE up -d --build
	say "Waiting for PieBB to answer"
	healthy
}

discard_test_files() {
	# back to exactly what git has: removes files from a test build, keeps ignored ones (.env)
	git reset --quiet --hard
	git clean -fdq
}

[ -d .git ] || die "$DIR is not a git clone of PieBB (git clone https://github.com/DaDosDude/PieBB.git)"
command -v git >/dev/null || die "git is needed"
command -v curl >/dev/null || die "curl is needed"
[ -f .env ] || die "no .env here: copy .env.example to .env and fill it in first"

mode=release force=0 tarball=""
while [ $# -gt 0 ]; do
	case "$1" in
		--test) [ $# -ge 2 ] || die "--test needs a .tar.gz file"; mode=test; tarball="$2"; shift 2 ;;
		--force) force=1; shift ;;
		-h|--help) usage ;;
		*) echo "unknown option: $1" >&2; usage 1 ;;
	esac
done

current="$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD)"
dirty=0
[ -z "$(git status --porcelain)" ] || dirty=1

if [ "$mode" = test ]; then
	[ -f "$tarball" ] || die "$tarball not found"
	tar tzf "$tarball" >/dev/null 2>&1 || die "$tarball is not a .tar.gz file"
	top="$(tar tzf "$tarball" 2>/dev/null | head -1 || true)"   # head stops tar early: not an error
	strip=0
	case "$top" in */*) strip=1 ;; esac   # packages hold a top-level piebb/ folder
	discard_test_files                     # start from the release, not from an earlier test
	say "Unpacking $tarball over $current (test build, not committed)"
	tar xzf "$tarball" --strip-components="$strip" --exclude='.env'
	if build; then
		say "Test build is running. Run ./update.sh to go back to the latest release."
		exit 0
	fi
	echo "PieBB did not start with this test build; going back to $current." >&2
	$COMPOSE logs --tail 30 >&2 || true
	discard_test_files
	build || die "$current did not start either; see: $COMPOSE logs"
	exit 1
fi

say "Checking for releases"
git fetch --quiet --tags --force origin || die "couldn't reach the PieBB repository"
latest="$(git tag -l 'v*' --sort=-v:refname | head -1 || true)"
[ -n "$latest" ] || die "no releases found"

if [ "$current" = "$latest" ] && [ "$dirty" = 0 ] && [ "$force" = 0 ]; then
	say "Already on the latest release, $latest."
	exit 0
fi

previous="$(git rev-parse HEAD)"
[ "$dirty" = 0 ] || say "Discarding the test build"
discard_test_files
if [ "$current" = "$latest" ]; then say "Back to $latest"; else say "Switching from $current to $latest"; fi
git -c advice.detachedHead=false checkout --quiet "$latest"
if build; then
	say "PieBB $latest is running."
	exit 0
fi

echo "PieBB $latest did not start; going back to $current." >&2
$COMPOSE logs --tail 30 >&2 || true
git -c advice.detachedHead=false checkout --quiet "$previous"
build || die "$current did not start either; see: $COMPOSE logs"
exit 1
