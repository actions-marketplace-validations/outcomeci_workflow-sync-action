import json

import httpx
import pytest

from outcomeci_sync.main import check_head, context, run
from outcomeci_sync.package import Snapshot, SyncError


@pytest.fixture
def github_env(repo, tmp_path):
    snapshot = Snapshot(repo({}))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"repository": {"id": 123, "full_name": "org/repo"}}))
    return snapshot, {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": snapshot.sha,
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_REPOSITORY_ID": "123",
        "GITHUB_EVENT_PATH": str(event),
    }


def test_sync_context_records_stable_repository_id(github_env):
    snapshot, env = github_env
    assert context(snapshot, env)["repository_id"] == "123"
    env["GITHUB_EVENT_NAME"] = "workflow_dispatch"
    assert context(snapshot, env)["commit_sha"] == snapshot.sha


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_EVENT_NAME", "pull_request_target"),
        ("GITHUB_EVENT_NAME", "schedule"),
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_SHA", "b" * 40),
        ("GITHUB_REPOSITORY_ID", "999"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_SERVER_URL", "https://github.enterprise.example"),
    ],
)
def test_untrusted_sync_context_rejected(github_env, key, value):
    snapshot, env = github_env
    env[key] = value
    with pytest.raises(SyncError):
        context(snapshot, env)


def test_branch_must_still_point_at_commit(github_env):
    snapshot, env = github_env
    provenance = context(snapshot, env)
    for sha in [snapshot.sha, "stale"]:
        http = httpx.Client(
            transport=httpx.MockTransport(
                lambda _, sha=sha: httpx.Response(200, json={"object": {"sha": sha}})
            )
        )
        if sha == snapshot.sha:
            check_head(provenance, "github-token", http)
        else:
            with pytest.raises(SyncError, match="Branch has moved"):
                check_head(provenance, "github-token", http)


def test_validation_needs_no_credentials_or_api(repo, bundle, tmp_path, monkeypatch):
    source = bundle()
    root = repo({source.path: source.content, ".outcomeci/workflows/hello/greet.md": "Say hello"})

    def network_forbidden(*args, **kwargs):
        raise AssertionError("Validation must not call the API")

    monkeypatch.setattr(httpx.Client, "request", network_forbidden)
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    run(
        {
            "GITHUB_WORKSPACE": str(root),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
    )
    assert "validated=1\n" in output.read_text()
    assert "published=[]\n" in output.read_text()
    assert "Validated **1**" in summary.read_text()
