# backup-sync

Once a day at 03:17 UTC (and on manual dispatch) the workflow backs up each GitHub repo
listed in [`repos.json`](repos.json):

```json
{
  "upstream": "morluto/rea",      // GitHub repo to back up
  "github": "TDHSC/rea-backup",   // private GitHub backup (optional)
  "codeberg": "rea-backup"        // Codeberg repo name, created automatically as public (optional)
}
```

What gets copied: all branches, all tags, and every release (notes + assets).
Nothing is ever deleted from the backups. If upstream force-pushes its default branch or
moves a tag, the old version is kept under `backup-archive/<UTC time>/<name>`
and a warning shows up in the workflow run. If upstream disappears, the run
fails (GitHub emails you) and the backups stay as they are.

## Adding a repo

1. Create the GitHub backup repo: private, empty, **not a fork**.
2. In that repo, disable Actions (Settings → Actions → General → Disable actions),
   otherwise the mirrored upstream workflows run there on every push.
3. Add the repo to the `BACKUP_GITHUB_TOKEN` fine-grained token's repository list.
4. Add an entry to `repos.json`, then run the workflow manually.

Codeberg only allows public repos for free/open-source content, so only add a
`codeberg` entry for projects under an OSI/FSF-approved license.

## Secrets

| Secret | What |
| --- | --- |
| `BACKUP_GITHUB_TOKEN` | Fine-grained PAT covering every GitHub backup repo: Contents read/write, Workflows read/write |
| `CODEBERG_TOKEN` | Codeberg access token: repository read/write, user read/write. Codeberg steps are skipped if unset. |
