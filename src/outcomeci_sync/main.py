"""GitHub Action entrypoint. Inputs travel through environment, never shell code."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx

from .client import Client
from .package import Snapshot, SyncError, discover
from .sync import apply, ordered, plan


def log(message: str):
    # A workflow name may contain workflow-command syntax. Prefix and escape lines.
    print("outcomeci: " + message.replace("\r", " ").replace("\n", " "), flush=True)


def context(snapshot: Snapshot, env: dict) -> dict:
    branch = env.get("INPUT_BRANCH", "main")
    if env.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise SyncError("sync is only allowed on push or workflow_dispatch")
    if env.get("GITHUB_REF") != f"refs/heads/{branch}":
        raise SyncError("sync is only allowed on the configured branch")
    if env.get("GITHUB_SHA") != snapshot.sha:
        raise SyncError("Checked-out commit differs from the GitHub event SHA")
    repository = env.get("GITHUB_REPOSITORY", "")
    repository_id = env.get("GITHUB_REPOSITORY_ID", "")
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not repository_id.isdigit()
    ):
        raise SyncError("Missing GitHub repository identity")
    if env.get("GITHUB_SERVER_URL", "https://github.com") != "https://github.com":
        raise SyncError("This release supports GitHub.com repositories")
    try:
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
        event_repo = event["repository"]
        if str(event_repo["id"]) != repository_id or event_repo["full_name"] != repository:
            raise SyncError("GitHub event repository does not match the checkout context")
    except (KeyError, OSError, ValueError) as exc:
        raise SyncError("Cannot verify GitHub event repository") from exc
    return {
        "repository": repository,
        "repository_id": repository_id,
        "ref": env["GITHUB_REF"],
        "commit_sha": snapshot.sha,
    }


def check_head(provenance: dict, token: str, http=None):
    if not token:
        raise SyncError("github-token is required to verify the current branch head")
    branch = provenance["ref"].removeprefix("refs/heads/")
    url = f"https://api.github.com/repos/{provenance['repository']}/git/ref/heads/{quote(branch, safe='')}"
    client = http or httpx.Client(timeout=30, follow_redirects=False)
    try:
        response = client.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise SyncError(f"GitHub branch check failed (HTTP {response.status_code})")
        if response.json()["object"]["sha"] != provenance["commit_sha"]:
            raise SyncError("Branch has moved since this run; sync the newest commit")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SyncError("Unable to verify the current GitHub branch head") from exc


def output(env, name, value):
    if env.get("GITHUB_OUTPUT"):
        with Path(env["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"{name}={json.dumps(value, separators=(',', ':'))}\n")


def run(env):
    mode = env.get("INPUT_MODE", "validate")
    if mode not in {"validate", "sync"}:
        raise SyncError("mode must be validate or sync")
    snapshot = Snapshot(Path(env.get("GITHUB_WORKSPACE", ".")))
    bundles = discover(snapshot, env.get("INPUT_DIRECTORY", ".outcomeci/workflows"))
    ordered(bundles)
    log(f"Validated {len(bundles)} committed workflow bundles at {snapshot.sha}")
    published, unchanged, removed = [], [], []
    if mode == "sync":
        provenance = context(snapshot, env)

        def verify():
            check_head(provenance, env.get("INPUT_GITHUB_TOKEN", ""))

        verify()
        client = Client(
            env.get("INPUT_API_URL", "https://api.outcomeci.com"),
            env.get("INPUT_WORKSPACE_ID", ""),
            env.get("INPUT_TOKEN", ""),
        )
        adopt = {
            name.strip()
            for name in env.get("INPUT_ADOPT_WORKFLOWS", "").splitlines()
            if name.strip()
        }
        prepared = plan(client, bundles, provenance, adopt)
        unchanged, removed = prepared.unchanged, prepared.removed
        log(
            f"Plan: {len(prepared.writes)} publish, {len(unchanged)} unchanged, {len(removed)} removed locally"
        )
        published = apply(client, prepared, verify, log)
        for name in removed:
            log(f"Removed locally; retained in OutcomeCI: {name}")
    for name, value in {
        "validated": len(bundles),
        "published": published,
        "unchanged": unchanged,
        "removed": removed,
    }.items():
        output(env, name, value)
    if env.get("GITHUB_STEP_SUMMARY"):
        with Path(env["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(
                f"### OutcomeCI workflow {mode}\n\n"
                f"Validated **{len(bundles)}** bundles. Published **{len(published)}**; "
                f"unchanged **{len(unchanged)}**; removed locally **{len(removed)}** "
                "(retained in OutcomeCI).\n"
            )


def main():
    try:
        run(os.environ)
    except SyncError as exc:
        log(str(exc))
        return 1
    except Exception:
        # Do not let tracebacks disclose tokens, response bodies, or workflow content.
        log("Unexpected sync failure; inspect the action version and API availability")
        return 1
    return 0
