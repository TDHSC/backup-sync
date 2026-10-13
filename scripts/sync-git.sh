#!/usr/bin/env bash
# Push every upstream branch and tag from a bare clone to a backup remote.
#
# Usage: sync-git.sh <bare-clone-dir> <remote-name> <remote-url>
#
# The backup never loses history:
#   - refs deleted upstream are left alone in the backup;
#   - when upstream force-pushes its default branch or moves a tag, the backup's
#     old version is first saved under refs/{heads,tags}/backup-archive/<UTC time>/<name>.
#     Other branches (feature branches get rebased all the time) are just overwritten.
set -euo pipefail

mirror=$1 name=$2 url=$3
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
while read -r ref old; do
  short=${ref#"refs/backup/$name/"}   # heads/<branch> or tags/<tag>
  kind=${short%%/*}
  rname=${short#*/}
  [[ $rname == "$archive_prefix"/* ]] && continue
  new=$(git rev-parse -q --verify "refs/$short" || true)
  # Deleted upstream (keep backup copy) or unchanged.
  [[ -z $new || $new == "$old" ]] && continue
  if [[ $kind == heads ]]; then
    [[ $rname != "$default_branch" ]] && continue
    # Ordinary fast-forward.
    git merge-base --is-ancestor "$old" "$new" 2>/dev/null && continue
  fi
  dest="refs/$kind/$archive_prefix/$ts/$rname"
  archive+=("$old:$dest")
  echo "::warning::$name: upstream rewrote $short; previous version kept at $dest"
done < <(git for-each-ref --format='%(refname) %(objectname)' "refs/backup/$name/")

if ((${#archive[@]})); then
  git push --quiet "$name" "${archive[@]}"
fi

git push --quiet --force "$name" 'refs/heads/*:refs/heads/*' 'refs/tags/*:refs/tags/*'
echo "$name: pushed $(git for-each-ref refs/heads | wc -l) branches, $(git for-each-ref refs/tags | wc -l) tags"
