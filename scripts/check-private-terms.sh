#!/usr/bin/env bash
# Blocks a commit or push that mentions a private term (CLAUDE.md, Privacy rules).
# Terms live in the gitignored .private-terms, one per line, matched case-insensitively;
# lines starting with # are comments. A missing or empty list blocks everything
# (fail closed), and the term itself is never printed.
#
#   check-private-terms.sh staged          pre-commit: staged changes, file names, branch
#   check-private-terms.sh message <file>  commit-msg: the commit message
#   check-private-terms.sh push            pre-push: commits and refs read from stdin
set -euo pipefail

root=$(git rev-parse --show-toplevel)
list="$root/.private-terms"
# Trimmed (spaces, Windows line endings), so a stray space can't silently disable a term.
terms=$(tr -d '\r' 2>/dev/null < "$list" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' \
  | grep -v '^#' | sed '/^$/d' || true)
if [ -z "$terms" ]; then
  echo "private-terms: $list is missing or empty, so nothing can be checked. Blocked." >&2
  echo "Create it (it's gitignored) with one private term per line." >&2
  exit 1
fi

blocked() {
  echo "private-terms: blocked. $1 contains a private term (not shown here)." >&2
  echo "Find it with: grep -inF -f .private-terms <file>   (or check the message/branch)" >&2
  exit 1
}

# Reads text on stdin; blocks if any term appears in it.
scan() {
  if grep -aqiF -f <(printf '%s\n' "$terms"); then blocked "$1"; fi  # -a: binary as text
}

case "${1:-}" in
  staged)
    # --text: binary files too (otherwise git prints "Binary files differ" and skips them)
    git diff --cached --no-color --text -U0 | { grep -a '^+' || true; } | scan "A staged change"
    git diff --cached --name-only | scan "A staged file name"
    (git symbolic-ref --short HEAD 2>/dev/null || true) | scan "The branch name"
    ;;
  message)
    scan "The commit message" < "$2"
    ;;
  push)
    zero=0000000000000000000000000000000000000000
    while read -r local_ref local_sha remote_ref remote_sha; do
      printf '%s\n%s\n' "$local_ref" "$remote_ref" | scan "A branch or tag name being pushed"
      [ "$local_sha" = "$zero" ] && continue  # deleting a remote branch: nothing to scan
      if [ "$(git cat-file -t "$local_sha")" = tag ]; then  # an annotated tag's own message
        git cat-file -p "$local_sha" | scan "An annotated tag"
      fi
      if [ "$remote_sha" = "$zero" ]; then
        range=$(git rev-list "$local_sha" --not --remotes)
      else
        range=$(git rev-list "$remote_sha..$local_sha")
      fi
      for sha in $range; do
        git show --no-color --text --format='%B' "$sha" | scan "Commit ${sha:0:7}"
      done
    done
    ;;
  *)
    echo "usage: $0 staged | message <file> | push" >&2
    exit 2
    ;;
esac
