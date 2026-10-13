"""Preflight ownership and bundle changes, then append revisions with CAS."""

from dataclasses import dataclass, field

from .package import Bundle, SyncError, digest, document


def owned(lineage: dict, repository_id: str) -> bool:
    return (
        lineage.get("source") == "github_action"
        and str(lineage.get("repository_id")) == repository_id
    )


def targets(steps):
    for entry in steps:
        for step in entry.values():
            if isinstance(step.get("dispatch"), str):
                yield step["dispatch"]


def ordered(bundles: list[Bundle], external: dict[str, dict] | None = None) -> list[Bundle]:
    """Validate local relationships; in sync mode also resolve external children."""
    by_name = {bundle.name: bundle for bundle in bundles}
    done, visiting, result = set(), set(), []

    def visit(name):
        if name in visiting:
            raise SyncError(f"Dispatcher dependency cycle involving {name}")
        if name in done:
            return
        visiting.add(name)
        bundle = by_name[name]
        for target in sorted(set(targets(bundle.definition.get("steps", [])))):
            child = by_name.get(target)
            definition = child.definition if child else (external or {}).get(target)
            if definition is None:
                if external is not None:
                    raise SyncError(f"Dispatcher {name} references missing workflow {target}")
                continue
            if definition.get("trigger") != {"type": "dispatcher", "dispatcher": name}:
                raise SyncError(f"Workflow {target} must declare dispatcher trigger {name}")
            if child:
                visit(target)
        visiting.remove(name)
        done.add(name)
        result.append(bundle)

    for name in sorted(by_name):
        visit(name)
    return result


@dataclass
class Plan:
    writes: list[dict] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def plan(client, bundles: list[Bundle], provenance: dict, adopt: set[str]) -> Plan:
    repository_id = provenance["repository_id"]
    rows = client.revisions()
    latest_rows = {}
    historical = {}
    for row in rows:
        name = row["name"]
        if name not in latest_rows or row["revision"] > latest_rows[name]["revision"]:
            latest_rows[name] = row
        if owned(row.get("lineage") or {}, repository_id) and (
            name not in historical or row["revision"] > historical[name]["revision"]
        ):
            historical[name] = row
    local = {bundle.name: bundle for bundle in bundles}
    unknown_adoptions = adopt - local.keys()
    if unknown_adoptions:
        raise SyncError("adopt-workflows contains names absent from the repository")
    old_paths = {row["lineage"].get("path"): name for name, row in historical.items()}
    remote = {}
    needed = set(local)
    for bundle in bundles:
        needed.update(targets(bundle.definition.get("steps", [])))
    for name in needed & latest_rows.keys():
        remote[name] = client.latest(latest_rows[name]["workflow_id"])
    external = {name: document(row["content"]) for name, row in remote.items() if name not in local}
    result = Plan(removed=sorted(historical.keys() - local.keys()))
    for bundle in ordered(bundles, external):
        previous_name = old_paths.get(bundle.path)
        if previous_name and previous_name != bundle.name:
            raise SyncError(
                f"Workflow name changed at {bundle.path}; migrate its identity explicitly"
            )
        row = remote.get(bundle.name)
        body = {
            "name": bundle.name,
            "content": bundle.content,
            "content_type": "yaml",
            "source_filename": bundle.filename,
            "files": bundle.files,
            "mode": "create" if row is None else "version",
            "lineage": {
                **provenance,
                "source": "github_action",
                "path": bundle.path,
                "bundle_sha256": bundle.digest,
            },
        }
        if row:
            lineage = row.get("lineage") or {}
            remote_digest = digest(row["content"], row["content_type"], row.get("files") or {})
            if not owned(lineage, repository_id):
                # Adoption is only for unowned content. Never take another repo's workflow.
                if lineage.get("source") == "github_action":
                    raise SyncError(f"Workflow {bundle.name} belongs to another repository")
                if bundle.name not in adopt or remote_digest != bundle.digest:
                    raise SyncError(
                        f"Workflow {bundle.name} is not repository-owned; exact-match adoption required"
                    )
            else:
                if remote_digest != lineage.get("bundle_sha256"):
                    raise SyncError(
                        f"Cloud edits detected for {bundle.name}; reconcile before syncing"
                    )
                if remote_digest == bundle.digest and lineage.get("path") == bundle.path:
                    result.unchanged.append(bundle.name)
                    continue
            body.update(
                expected_parent_revision=row["revision"],
                expected_parent_sha256=row["content_sha256"],
            )
        result.writes.append(body)
    return result


def apply(client, prepared: Plan, check_head, report) -> list[str]:
    completed = []
    for body in prepared.writes:
        try:
            check_head()
            client.publish(body)
        except SyncError as exc:
            raise SyncError(
                f"{exc}. Successfully published before failure: {', '.join(completed) or 'none'}. "
                "Sync is not atomic; retry after resolving the failure."
            ) from exc
        completed.append(body["name"])
        report(f"Published {body['name']}")
    return completed
