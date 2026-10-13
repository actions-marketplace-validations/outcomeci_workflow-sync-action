"""Small authenticated client; redirects and response-body logging are forbidden."""

from urllib.parse import quote, urlsplit

import httpx

from .package import SyncError


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SyncError("api-url must be an HTTPS origin without a path or credentials")
    return value.rstrip("/")


class Client:
    def __init__(self, api_url: str, workspace: str, token: str, http=None):
        if not workspace or not token:
            raise SyncError("sync requires workspace-id and token")
        self.base = (
            f"{origin(api_url)}/v1/workspaces/{quote(workspace, safe='')}/workflow-revisions"
        )
        self.token = token
        self.http = http or httpx.Client(timeout=60, follow_redirects=False)

    def request(self, method: str, suffix: str = "", body=None):
        try:
            response = self.http.request(
                method,
                self.base + suffix,
                headers={"Authorization": f"Bearer {self.token}"},
                json=body,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise SyncError("OutcomeCI request failed; check connectivity and retry") from exc
        if response.status_code == 409:
            raise SyncError("OutcomeCI revision conflict; re-run against the latest branch commit")
        if not 200 <= response.status_code < 300:
            raise SyncError(f"OutcomeCI {method} failed (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise SyncError("OutcomeCI returned invalid JSON") from exc

    def revisions(self):
        return self.request("GET")

    def latest(self, workflow_id):
        return self.request("GET", f"/{quote(workflow_id, safe='')}/latest")

    def publish(self, body):
        return self.request("POST", body=body)
