#!/usr/bin/env bash
# Push the upstream default branch and every tag from a bare clone to a backup remote.
#
# Usage: sync-git.sh <bare-clone-dir> <remote-name> <remote-url> [--prune-other-branches]
#
# The backup never loses history:
#   - tags deleted upstream are left alone in the backup;
#   - when upstream force-pushes its default branch or moves a tag, the backup's
#     old version is first saved under refs/{heads,tags}/backup-archive/<UTC time>/<name>.
#
# --prune-other-branches deletes backup branches other than the default branch
# and backup-archive/*. Only for one-off cleanups: if upstream ever renames its
# default branch, the old one would be deleted.
set -euo pipefail

mirror=$1 name=$2 url=$3 prune=${4:-}
archive_prefix=backup-archive
ts=$(date -u +%Y%m%dT%H%M%SZ)

cd "$mirror"
default_branch=$(git symbolic-ref --short HEAD)
git remote remove "$name" 2>/dev/null || true
git remote add "$name" "$url"

# Snapshot what the backup currently has into a private namespace.
git for-each-ref --format='%(refname)' "refs/backup/$name/" | xargs -r -n1 git update-ref -d
git fetch --quiet --no-tags "$name" \
  "+refs/heads/*:refs/backup/$name/heads/*" \
  "+refs/tags/*:refs/backup/$name/tags/*"

archive=()
delete=()
while read -r ref old; do
  short=${ref#"refs/backup/$name/"}   # heads/<branch> or tags/<tag>
  kind=${short%%/*}
  rname=${short#*/}
  [[ $rname == "$archive_prefix"/* ]] && continue
  if [[ $kind == heads && $rname != "$default_branch" ]]; then
    [[ $prune == --prune-other-branches ]] && delete+=(":refs/heads/$rname")
    continue
  fi
  new=$(git rev-parse -q --verify "refs/$short" || true)
  # Deleted upstream (keep backup copy) or unchanged.
  [[ -z $new || $new == "$old" ]] && continue
  # Ordinary fast-forward of the default branch.
  [[ $kind == heads ]] && git merge-base --is-ancestor "$old" "$new" 2>/dev/null && continue
  dest="refs/$kind/$archive_prefix/$ts/$rname"
  archive+=("$old:$dest")
  echo "::warning::$name: upstream rewrote $short; previous version kept at $dest"
done < <(git for-each-ref --format='%(refname) %(objectname)' "refs/backup/$name/")

if ((${#archive[@]})); then
  git push --quiet "$name" "${archive[@]}"
fi

git push --quiet --force "$name" "refs/heads/$default_branch:refs/heads/$default_branch" 'refs/tags/*:refs/tags/*'
echo "$name: pushed $default_branch and $(git for-each-ref refs/tags | wc -l) tags"

if ((${#delete[@]})); then
  git push --quiet "$name" "${delete[@]}"
  echo "$name: deleted ${#delete[@]} other branches"
fi
