import hashlib
import json

import httpx
import pytest

from outcomeci_sync.client import Client
from outcomeci_sync.package import SyncError
from outcomeci_sync.sync import apply, ordered, plan, targets

PROVENANCE = {
    "repository_id": "123",
    "repository": "org/repo",
    "ref": "refs/heads/main",
    "commit_sha": "a" * 40,
}


class Service:
    """Exercise the real HTTP client against the existing revision API contract."""

    def __init__(self):
        self.rows = []
        self.posts = []
        self.fail_name = None
        self.client = Client(
            "https://api.example.com",
            "workspace_test",
            "secret-token",
            httpx.Client(transport=httpx.MockTransport(self.request)),
        )

    def request(self, request):
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.url.path.startswith("/v1/workspaces/workspace_test/workflow-revisions")
        if request.method == "GET":
            if request.url.path.endswith("/latest"):
                workflow_id = request.url.path.split("/")[-2]
                return httpx.Response(
                    200,
                    json=next(
                        row for row in reversed(self.rows) if row["workflow_id"] == workflow_id
                    ),
                )
            return httpx.Response(200, json=self.rows)
        body = json.loads(request.content)
        if body["name"] == self.fail_name:
            return httpx.Response(409, json={"detail": "DO_NOT_LOG_BODY"})
        prior = next((row for row in reversed(self.rows) if row["name"] == body["name"]), None)
        if prior:
            assert body["mode"] == "version"
            if (
                body["expected_parent_revision"] != prior["revision"]
                or body["expected_parent_sha256"] != prior["content_sha256"]
            ):
                return httpx.Response(409)
        else:
            assert body["mode"] == "create"
        self.posts.append(body)
        row = {
            **body,
            "workflow_id": body["name"],
            "revision": prior["revision"] + 1 if prior else 1,
            "content_sha256": hashlib.sha256(body["content"].encode()).hexdigest(),
        }
        self.rows.append(row)
        return httpx.Response(201, json=row)

    def seed(self, bundle):
        apply(
            self.client,
            plan(self.client, [bundle], PROVENANCE, set()),
            lambda: None,
            lambda _: None,
        )


def publish(service, bundles, adopt=()):
    prepared = plan(service.client, bundles, PROVENANCE, set(adopt))
    apply(service.client, prepared, lambda: None, lambda _: None)
    return prepared


def test_create_repeat_and_instruction_only_version(bundle):
    service = Service()
    publish(service, [bundle()])
    assert service.posts[0]["files"] == bundle().files
    assert publish(service, [bundle()]).unchanged == ["hello"]
    assert len(service.posts) == 1
    publish(service, [bundle(instruction="New instructions")])
    assert service.posts[-1]["expected_parent_revision"] == 1
    assert len(service.posts) == 2
    assert service.rows[-1]["lineage"]["repository_id"] == "123"


def test_cloud_edits_and_foreign_ownership_preflight_before_any_writes(bundle):
    for modification in [{"bundle_sha256": "bad"}, {"repository_id": "999"}]:
        service = Service()
        service.seed(bundle())
        service.rows[-1]["lineage"].update(modification)
        with pytest.raises(SyncError):
            publish(service, [bundle("first"), bundle()], adopt=["hello"])
        assert len(service.posts) == 1


def test_unowned_requires_explicit_exact_match_adoption(bundle):
    service = Service()
    service.seed(bundle())
    service.rows[-1]["lineage"] = {}
    with pytest.raises(SyncError, match="adoption"):
        publish(service, [bundle()])
    with pytest.raises(SyncError, match="adoption"):
        publish(service, [bundle(instruction="changed")], ["hello"])
    publish(service, [bundle()], ["hello"])
    assert service.rows[-1]["revision"] == 2


def test_removal_retains_cloud_and_rename_fails(bundle):
    service = Service()
    service.seed(bundle())
    assert publish(service, []).removed == ["hello"]
    assert len(service.posts) == 1
    with pytest.raises(SyncError, match="name changed"):
        publish(service, [bundle("renamed", path=bundle().path)])


def test_move_records_new_path_without_changing_identity(bundle):
    service = Service()
    service.seed(bundle())
    moved = bundle(path=".outcomeci/workflows/new/workflow.yml")
    publish(service, [moved])
    assert service.rows[-1]["lineage"]["path"] == moved.path
    assert publish(service, [moved]).unchanged == ["hello"]


def test_concurrent_write_rejected_by_cas(bundle):
    service = Service()
    service.seed(bundle())
    prepared = plan(service.client, [bundle(instruction="next")], PROVENANCE, set())
    service.rows[-1]["revision"] += 1
    with pytest.raises(SyncError, match="revision conflict"):
        apply(service.client, prepared, lambda: None, lambda _: None)
    assert len(service.posts) == 1


def test_dispatcher_published_after_children_and_missing_rejected(bundle):
    parent = bundle("a-router", targets=["z-child"])
    child = bundle("z-child", dispatcher="a-router")
    service = Service()
    publish(service, [parent, child])
    assert [body["name"] for body in service.posts] == ["z-child", "a-router"]
    assert [item.name for item in ordered([parent])] == ["a-router"]
    with pytest.raises(SyncError, match="missing workflow"):
        publish(Service(), [parent])
    with pytest.raises(SyncError, match="must declare"):
        publish(Service(), [parent, bundle("z-child")])


def test_existing_external_dispatch_child_is_checked(bundle):
    service = Service()
    service.seed(bundle("child", dispatcher="parent"))
    publish(service, [bundle("parent", targets=["child"])])
    assert service.posts[-1]["name"] == "parent"


def test_dispatch_payload_is_not_a_dependency():
    assert list(targets([{"step": {"dispatch": "child", "with": {"dispatch": "data"}}}])) == [
        "child"
    ]


def test_partial_failure_reports_completed_and_retry_is_idempotent(bundle):
    service = Service()
    service.fail_name = "b"
    with pytest.raises(SyncError, match="Successfully published before failure: a") as error:
        publish(service, [bundle("a"), bundle("b")])
    assert "DO_NOT_LOG_BODY" not in str(error.value)
    service.fail_name = None
    prepared = publish(service, [bundle("a"), bundle("b")])
    assert prepared.unchanged == ["a"]
    assert [body["name"] for body in service.posts] == ["a", "b"]


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "https://user:pass@example.com",
        "https://example.com/v1",
        "https://example.com?key=secret",
    ],
)
def test_api_url_must_be_https_origin(url):
    with pytest.raises(SyncError):
        Client(url, "workspace", "token")


def test_auth_never_follows_redirect():
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://attacker.example.com"})

    client = Client(
        "https://api.example.com",
        "workspace",
        "token",
        httpx.Client(transport=httpx.MockTransport(redirect)),
    )
    with pytest.raises(SyncError, match="HTTP 302"):
        client.revisions()
    assert len(requests) == 1
