# Releasing the action

1. Merge tested changes into `main`. Set the version in `pyproject.toml` and
   update README/example action references for a new version.
2. Tag that reviewed commit with an annotated stable version, such as
   `git tag -a v1.0.0 -m 'OutcomeCI Workflow Sync v1.0.0'`, then push the tag.
3. The Release workflow runs CI, checks the tag against the package version,
   and creates a GitHub release with generated notes. Keep version tags
   immutable. Consumers can pin either the version tag or its full commit SHA.
4. To list the action on GitHub Marketplace, edit the release in GitHub, choose
   the Marketplace publishing option, accept the publisher agreement if
   prompted, select relevant categories, and publish. Confirm that the listing
   resolves before announcing Marketplace availability.

This repo contains one root `action.yml`, an MIT license, description, author,
branding, documentation, usage examples, and public CI. Marketplace eligibility
and the account's agreement must still be confirmed in GitHub's release UI.

Reference: [Publishing actions in GitHub Marketplace](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace).

Release validation does not require an OutcomeCI key or publish example
workflows. Live workspace integration testing requires a separately configured
workspace and credential; never add production credentials to test fixtures.
