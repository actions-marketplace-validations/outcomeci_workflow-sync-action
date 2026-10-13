# Repository workflow sync

## Scope and acceptance

An independently maintained GitHub Action publishes repo-owned workflows to
an existing OutcomeCI workspace. Consumers store one YAML file and sibling
Markdown instructions in each `.outcomeci/workflows/<workflow>/` folder.
The action must validate PRs without service credentials, publish committed
changes on the configured branch, preserve immutable revision history, skip
identical bundles, and fail before writes on invalid content or ownership
conflicts. No CLI or API source changes are required.

## Implementation

`package.py` reads Git objects at HEAD and stages canonical compiler bundles
in a temporary directory. It rejects duplicate YAML keys, aliases, traversal,
symlink payloads, duplicate names, ambiguous definitions, missing instructions,
and API size-limit violations. Markdown files map to the compiler's existing
`.outcomeci/instructions/` namespace. Compilation does not execute workflows.

`sync.py` reads revision history and latest complete bundles. The source
identity is `(workspace, YAML name)`; ownership is the immutable GitHub
repository ID recorded in lineage. Bundle digests include YAML and support
files. Revision history also identifies removals and accidental name changes.
Exact-match adoption binds previously unowned workflows. A topological sort
orders dispatch children before parents. All preflight checks complete before
POST requests begin.

`client.py` calls existing `/v1/workspaces/{workspace}/workflow-revisions`
GET/POST and `/{workflow_id}/latest` GET endpoints. Version requests include
both `expected_parent_revision` and `expected_parent_sha256`. Requests never
follow redirects or log response bodies. Individual revisions are atomic;
multi-workflow sync is not. Partial progress is reported for safe retries.

`main.py` enforces event, branch, checkout SHA, event repository identity, and
live branch-head constraints. The composite action passes inputs through the
environment and runs trusted action code with isolated Python imports. It
does not write authentication configuration. Tokenless validation uses the
same package/compiler implementation as sync.

## Validation plan

Tests build real Git fixtures, compile real instruction bundles with the
pinned public CLI, and run HTTP synchronization against a contract-faithful
mock API. Cases cover changes and retries, support-file changes, adoption,
ownership/drift, concurrent updates, deletion retention, dispatcher ordering,
partial failures, event guards, stale commits, and credential redirects. CI
also executes the composite action against the committed example.

Native GitHub App installation, server-enforced repo claims/OIDC, credential
grants, deletion, rollback, and public workflow publication are deferred.
