#!/usr/bin/env python3
"""Copy upstream GitHub releases (notes and assets) to the backup repos.

Usage:
  sync_releases.py github           # env: UPSTREAM_REPO, GITHUB_BACKUP_REPO, UPSTREAM_TOKEN, BACKUP_GITHUB_TOKEN
  sync_releases.py codeberg-ensure  # env: CODEBERG_TOKEN, CODEBERG_BACKUP_REPO; prints the owner login
  sync_releases.py codeberg         # env: UPSTREAM_REPO, UPSTREAM_TOKEN, CODEBERG_TOKEN, CODEBERG_OWNER, CODEBERG_BACKUP_REPO

Releases that already exist in the backup are left untouched.
"""
import json
import mimetypes
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

GITHUB_API = "https://api.github.com"
CODEBERG_API = "https://codeberg.org/api/v1"


def request(method, url, token, scheme="Bearer", data=None, headers=None, raw=False):
    hdrs = {"Accept": "application/json", "User-Agent": "backup-sync"}
    if token:
        hdrs["Authorization"] = f"{scheme} {token}"
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        sys.exit(f"{method} {url} -> {e.code}: {e.read()[:500].decode(errors='replace')}")
    if raw:
        return body
    return json.loads(body) if body else {}


def paginate(url, token, scheme="Bearer", per_page_key="per_page", per_page=100):
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        items = request("GET", f"{url}{sep}{per_page_key}={per_page}&page={page}", token, scheme) or []
        yield from items
        if len(items) < per_page:
            return
        page += 1


def upstream_releases():
    repo, token = os.environ["UPSTREAM_REPO"], os.environ.get("UPSTREAM_TOKEN")
    releases = [r for r in paginate(f"{GITHUB_API}/repos/{repo}/releases", token) if not r["draft"]]
    latest = request("GET", f"{GITHUB_API}/repos/{repo}/releases/latest", token)
    releases.sort(key=lambda r: r["published_at"] or r["created_at"])  # oldest first
    return releases, (latest or {}).get("id")


def notes(rel):
    repo = os.environ["UPSTREAM_REPO"]
    footer = (
        f"\n\n---\n_Backup of [{repo}@{rel['tag_name']}]({rel['html_url']}), "
        f"originally published {rel['published_at']}._"
    )
    return (rel["body"] or "") + footer


def download_asset(asset, dest):
    token = os.environ.get("UPSTREAM_TOKEN")
    # The API asset URL with octet-stream works for both public and private repos.
    data = request("GET", asset["url"], token, headers={"Accept": "application/octet-stream"}, raw=True)
    with open(dest, "wb") as f:
        f.write(data)


def sync_github():
    target, token = os.environ["GITHUB_BACKUP_REPO"], os.environ["BACKUP_GITHUB_TOKEN"]
    upstream, latest_id = upstream_releases()
    have = {r["tag_name"] for r in paginate(f"{GITHUB_API}/repos/{target}/releases", token)}
    created = 0
    for rel in upstream:
        if rel["tag_name"] in have:
            continue
        new = request("POST", f"{GITHUB_API}/repos/{target}/releases", token, data={
            "tag_name": rel["tag_name"],
            "name": rel["name"] or rel["tag_name"],
            "body": notes(rel),
            "prerelease": rel["prerelease"],
            "make_latest": "true" if rel["id"] == latest_id else "false",
        })
        with tempfile.TemporaryDirectory() as tmp:
            for asset in rel["assets"]:
                path = os.path.join(tmp, asset["name"])
                download_asset(asset, path)
                upload = new["upload_url"].split("{")[0] + "?" + urllib.parse.urlencode({"name": asset["name"]})
                with open(path, "rb") as f:
                    request("POST", upload, token, data=f.read(),
                            headers={"Content-Type": asset["content_type"] or "application/octet-stream"})
        print(f"github: created release {rel['tag_name']} ({len(rel['assets'])} assets)")
        created += 1
    print(f"github: {created} new releases, {len(upstream)} upstream total")


def codeberg_ensure():
    token, name = os.environ["CODEBERG_TOKEN"], os.environ["CODEBERG_BACKUP_REPO"]
    owner = request("GET", f"{CODEBERG_API}/user", token, scheme="token")["login"]
    if request("GET", f"{CODEBERG_API}/repos/{owner}/{name}", token, scheme="token") is None:
        upstream = request("GET", f"{GITHUB_API}/repos/{os.environ['UPSTREAM_REPO']}", os.environ.get("UPSTREAM_TOKEN"))
        request("POST", f"{CODEBERG_API}/user/repos", token, scheme="token", data={
            "name": name,
            "private": False,
            "default_branch": upstream["default_branch"],
            "description": f"Backup of https://github.com/{os.environ['UPSTREAM_REPO']}",
        })
        print(f"codeberg: created {owner}/{name}", file=sys.stderr)
    print(owner)


def multipart(field, filename, data, content_type):
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def sync_codeberg():
    token = os.environ["CODEBERG_TOKEN"]
    repo = f"{os.environ['CODEBERG_OWNER']}/{os.environ['CODEBERG_BACKUP_REPO']}"
    base = f"{CODEBERG_API}/repos/{repo}"
    # Forgejo makes the first pushed branch the default; match upstream instead.
    want = request("GET", f"{GITHUB_API}/repos/{os.environ['UPSTREAM_REPO']}", os.environ.get("UPSTREAM_TOKEN"))["default_branch"]
    if request("GET", base, token, scheme="token")["default_branch"] != want:
        request("PATCH", base, token, scheme="token", data={"default_branch": want})
        print(f"codeberg: default branch set to {want}")
    upstream, _ = upstream_releases()
    have = {r["tag_name"] for r in paginate(f"{base}/releases", token, scheme="token", per_page_key="limit", per_page=50)}
    created = 0
    for rel in upstream:
        if rel["tag_name"] in have:
            continue
        new = request("POST", f"{base}/releases", token, scheme="token", data={
            "tag_name": rel["tag_name"],
            "name": rel["name"] or rel["tag_name"],
            "body": notes(rel),
            "prerelease": rel["prerelease"],
        })
        with tempfile.TemporaryDirectory() as tmp:
            for asset in rel["assets"]:
                path = os.path.join(tmp, asset["name"])
                download_asset(asset, path)
                with open(path, "rb") as f:
                    ctype = asset["content_type"] or mimetypes.guess_type(asset["name"])[0] or "application/octet-stream"
                    body, header = multipart("attachment", asset["name"], f.read(), ctype)
                url = f"{base}/releases/{new['id']}/assets?" + urllib.parse.urlencode({"name": asset["name"]})
                request("POST", url, token, scheme="token", data=body, headers={"Content-Type": header})
        print(f"codeberg: created release {rel['tag_name']} ({len(rel['assets'])} assets)")
        created += 1
    print(f"codeberg: {created} new releases, {len(upstream)} upstream total")


if __name__ == "__main__":
    {"github": sync_github, "codeberg-ensure": codeberg_ensure, "codeberg": sync_codeberg}[sys.argv[1]]()
