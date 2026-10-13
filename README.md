# OutcomeCI Workflow Sync

Keep OutcomeCI workflows in your repository. Validate them in pull requests and
publish immutable workflow revisions when changes merge into `main`.

Each workflow is a folder containing **one YAML definition and its Markdown
instructions side by side**:

```text
.outcomeci/
  workflows/
    feature-intake/
      workflow.yml
      scope.md
      review.md
    weekly-report/
      workflow.yml
      summarize.md
```

Use a stable, workspace-unique `name` in each YAML file. Reference instructions
by filename, for example `reason: scope.md`. The action packages these files
for the OutcomeCI compiler and API automatically. Any `.yml` or `.yaml`
filename works; each workflow folder must contain exactly one definition.

## Set up

1. Add your workflow folders under `.outcomeci/workflows/`. See the
   [hello workflow](examples/.outcomeci/workflows/hello/workflow.yml) and its
   [instruction file](examples/.outcomeci/workflows/hello/greet.md).
2. Set the repository variable `OUTCOMECI_WORKSPACE_ID` to the destination
   workspace ID, and the repository secret `OUTCOMECI_TOKEN` to an OutcomeCI
   workspace key with workflow read/write access.
3. Copy [the complete GitHub workflow](examples/sync-workflows.yml) to
   `.github/workflows/outcomeci.yml`. It validates PRs without an OutcomeCI key
   and syncs changes on `main`. Commit both directories.

Minimal publishing step, after `actions/checkout`:

```yaml
- uses: outcomeci/workflow-sync-action@v1.0.0
  with:
    mode: sync
    workspace-id: ${{ vars.OUTCOMECI_WORKSPACE_ID }}
    token: ${{ secrets.OUTCOMECI_TOKEN }}
```

Use a full release commit SHA when you need an immutable action reference.
The action supports Linux runners with Bash and Python 3.12. GitHub Enterprise
Server, OIDC authentication, and other runner operating systems are outside
this release's supported configuration.

## Inputs and outputs

| Input | Default | Purpose |
| --- | --- | --- |
| `mode` | `validate` | `validate` or `sync` |
| `directory` | `.outcomeci/workflows` | Repository-relative workflow root |
| `workspace-id` | — | Destination workspace; required for sync |
| `token` | — | Workspace workflow-write key; required for sync |
| `api-url` | `https://api.outcomeci.com` | HTTPS API origin |
| `branch` | `main` | Branch permitted to sync |
| `github-token` | `${{ github.token }}` | Token with `contents: read` for branch freshness checks |
| `adopt-workflows` | — | Newline-separated existing workflow names to adopt on exact bundle match |

Outputs: `validated` is a count. `published`, `unchanged`, and `removed` are
JSON arrays of workflow names. The action also writes a GitHub job summary.
Outputs describe a successfully completed run; on failure, logs identify any
workflows already published.

## Sync behavior

- Only files committed in the checked-out Git revision are considered. The
  YAML and sibling `.md` files are included. Untracked files, working-tree
  changes, `.env` files, and runtime artifacts are excluded. Do not commit
  secrets into workflow YAML or Markdown; use OutcomeCI Vault references.
- Every bundle is validated with `outcomeci-cli==0.59.1` before writes begin.
  Canonical support-file paths are `.outcomeci/instructions/<filename>` inside
  the uploaded bundle. Inline instructions are also supported.
- A YAML or Markdown change creates a revision. Identical bundles are skipped.
  Moving a workflow records the new source path without changing its identity.
  Renaming a workflow at its existing path requires an explicit migration.
- GitHub's immutable repository ID identifies the source owner in revision
  lineage. Other repositories' workflows are never overwritten. A cloud
  workflow without matching ownership requires explicit adoption, and adoption
  requires an exact match of YAML and all instruction files. Adoption creates
  a revision recording ownership. Remove the adoption input afterward.
- Cloud edits stop publishing until reconciled. To adopt an existing workflow,
  first commit its exact cloud bundle in this layout and use `adopt-workflows`.
  Change its content only after adoption succeeds. Workflows whose support
  files cannot be represented in this layout need a manual migration.
- Dispatch targets in this repository are published before their dispatchers.
  Sync checks external targets against the destination workspace. Validation
  without credentials checks local relationships only.
- Removing a workflow folder reports the workflow as removed locally but
  retains it in OutcomeCI. An empty workflow root is valid and never deletes
  cloud workflows. Retirement is an explicit workspace operation.
- Publishing is sequential, not atomic. Updates use revision and content-hash
  preconditions. If a later write fails, earlier writes remain; fix the issue
  and rerun. Already-published bundles are skipped on retry.

Sync requires a `push` or `workflow_dispatch` event on the configured branch.
The checkout must match the event commit, and the action checks the live
branch head before planning and each write. Use the example's concurrency
group to serialize this repository's sync jobs. A branch may still move during
an in-flight API request; the next run reconciles the latest commit. Cross-repo
changes are not a transaction.

The workspace key is a workspace credential, **not a server-enforced
repository-scoped credential**. This action enforces its ownership policy on
the client. Store the key as a GitHub Actions secret and control changes to
the publishing job. Never use `pull_request_target` to run an untrusted checkout.

Publishing revisions does not create Vault grants, enable webhook endpoints,
start runs, or publish workflows to the public catalog. Trigger registrations
follow the existing OutcomeCI revision API behavior.

## Develop and release

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
.venv/bin/python -m pytest -q
```

Run local validation against committed files:

```sh
GITHUB_WORKSPACE="$PWD" INPUT_DIRECTORY=examples/.outcomeci/workflows \
  .venv/bin/python -I scripts/run.py
```

The action installs its pinned compiler in an isolated temporary environment.
It runs with Python isolated mode to avoid importing code from the consumer
checkout. Dependency resolution requires PyPI access; tokenless validation
does not call the OutcomeCI or GitHub APIs.

See [design](docs/design.md) and [release instructions](docs/releasing.md).
The repository includes action metadata, MIT licensing, CI, and an automated
GitHub release workflow. GitHub Marketplace listing is a separate publishing
step; a GitHub release alone does not list the action there.
