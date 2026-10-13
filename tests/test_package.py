import base64

import pytest

from outcomeci_sync.package import Snapshot, SyncError, discover, document, validate


def test_side_by_side_instructions_compile_from_commit(repo, bundle):
    source = bundle()
    root = repo(
        {
            source.path: source.content,
            ".outcomeci/workflows/hello/greet.md": "Committed instruction.",
            ".outcomeci/workflows/hello/.env": "DO_NOT_PACKAGE",
            ".outcomeci/workflows/hello/runtime.json": "DO_NOT_PACKAGE",
        }
    )
    (root / ".outcomeci/workflows/hello/greet.md").write_text("Uncommitted edit")
    (root / ".outcomeci/workflows/hello/secret.md").write_text("Untracked secret")
    (actual,) = discover(Snapshot(root))
    assert actual.files == {
        ".outcomeci/instructions/greet.md": base64.b64encode(b"Committed instruction.").decode()
    }


def test_instruction_only_change_changes_digest(bundle):
    assert bundle(instruction="one").digest != bundle(instruction="two").digest


@pytest.mark.parametrize("directory", ["/tmp", "../workflows", "a/../b", "./a", "a//b", "a\\b", ""])
def test_invalid_roots(repo, directory):
    with pytest.raises(SyncError, match="repository-relative"):
        discover(Snapshot(repo({})), directory)


def test_two_definitions_in_one_folder(repo, bundle):
    source = bundle()
    root = repo(
        {
            source.path: source.content,
            source.path.replace("workflow.yml", "extra.yml"): source.content,
        }
    )
    with pytest.raises(SyncError, match="exactly one"):
        discover(Snapshot(root))


def test_definition_requires_folder(repo, bundle):
    root = repo({".outcomeci/workflows/workflow.yml": bundle().content})
    with pytest.raises(SyncError, match="own folder"):
        discover(Snapshot(root))


def test_duplicate_workflow_names(repo, bundle):
    files = {}
    for folder in ["a", "b"]:
        files[f".outcomeci/workflows/{folder}/workflow.yml"] = bundle().content
        files[f".outcomeci/workflows/{folder}/greet.md"] = "Hi"
    with pytest.raises(SyncError, match="Duplicate workflow name"):
        discover(Snapshot(repo(files)))


@pytest.mark.parametrize("content", ["name: one\nname: two", "a: &a text\nb: *a", "[]"])
def test_ambiguous_yaml_rejected(content):
    with pytest.raises(SyncError):
        document(content)


def test_missing_and_outside_instructions_fail(repo, bundle):
    source = bundle()
    with pytest.raises(SyncError, match="sibling Markdown"):
        discover(Snapshot(repo({source.path: source.content})))
    content = source.content.replace("greet.md", "/tmp/secret.md")
    with pytest.raises(SyncError, match="sibling Markdown"):
        validate(type(source)(source.name, source.path, content, source.files, document(content)))


def test_symlink_instruction_rejected(repo, bundle):
    source = bundle()
    root = repo({source.path: source.content, "outside.md": "Not a sibling"})
    (root / ".outcomeci/workflows/hello/greet.md").symlink_to("../../../outside.md")
    repo({})
    with pytest.raises(SyncError, match="symlinks"):
        discover(Snapshot(root))


def test_empty_repository_is_valid_for_non_destructive_removal(repo):
    assert discover(Snapshot(repo({}))) == []
