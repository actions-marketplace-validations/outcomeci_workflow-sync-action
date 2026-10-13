"""Build canonical workflow packages from committed Git objects, never runtime files."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from outcomeci.workflow.compiler import compile_workflow


class SyncError(Exception):
    """A diagnostic safe to show without a request body or credential."""


class UniqueLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise SyncError("YAML aliases are not supported in workflow bundles")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        try:
            if len(keys) != len(set(keys)):
                raise SyncError("Workflow YAML contains duplicate mapping keys")
        except TypeError as exc:
            raise SyncError("Workflow YAML keys must be scalar values") from exc
        return super().construct_mapping(node, deep)


def document(content: str) -> dict:
    try:
        value = yaml.load(content, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        # YAML parser diagnostics can echo source lines, so do not print them.
        raise SyncError("Invalid workflow YAML") from exc
    if not isinstance(value, dict):
        raise SyncError("Workflow YAML must contain a mapping")
    return value


def digest(content: str, content_type: str, files: dict[str, str]) -> str:
    try:
        normalized = {
            name: base64.b64encode(base64.b64decode(value, validate=True)).decode()
            for name, value in files.items()
        }
    except (ValueError, TypeError) as exc:
        raise SyncError("Invalid support-file encoding in workflow revision") from exc
    body = json.dumps(
        {"content": content, "content_type": content_type, "files": normalized},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class Bundle:
    name: str
    path: str
    content: str
    files: dict[str, str]
    definition: dict

    @property
    def digest(self) -> str:
        return digest(self.content, "yaml", self.files)

    @property
    def filename(self) -> str:
        return PurePosixPath(self.path).name


class Snapshot:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.sha = self.git("rev-parse", "--verify", "HEAD").decode().strip()
        self.entries: dict[str, tuple[str, str]] = {}
        for row in self.git("ls-tree", "-r", "-z", self.sha).split(b"\0"):
            if not row:
                continue
            metadata, filename = row.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            try:
                name = filename.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SyncError("Repository paths must use UTF-8") from exc
            self.entries[name] = (mode if kind == "blob" else "unsupported", oid)

    def git(self, *args: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, check=False
        )
        if result.returncode:
            raise SyncError("Cannot read the checked-out Git commit; run actions/checkout first")
        return result.stdout

    def read(self, name: str, limit: int) -> bytes:
        mode, oid = self.entries[name]
        if mode not in {"100644", "100755"}:
            raise SyncError(f"Workflow bundles cannot contain symlinks or submodules: {name}")
        size = int(self.git("cat-file", "-s", oid))
        if size > limit:
            raise SyncError(f"Workflow file exceeds its size limit: {name}")
        return self.git("cat-file", "blob", oid)


def discover(snapshot: Snapshot, directory: str = ".outcomeci/workflows") -> list[Bundle]:
    path = PurePosixPath(directory)
    if (
        path.is_absolute()
        or not directory
        or any(part in {"", ".", ".."} for part in directory.split("/"))
        or "\\" in directory
        or any(ord(char) < 32 for char in directory)
    ):
        raise SyncError("directory must be a repository-relative folder without traversal")
    groups: dict[PurePosixPath, list[str]] = {}
    for name in sorted(snapshot.entries):
        candidate = PurePosixPath(name)
        if name.startswith(f"{path}/") and candidate.suffix.lower() in {".yml", ".yaml"}:
            if candidate.parent == path:
                raise SyncError(f"Each workflow needs its own folder: {name}")
            groups.setdefault(candidate.parent, []).append(name)
    bundles = []
    names: set[str] = set()
    for folder, definitions in groups.items():
        if len(definitions) != 1:
            raise SyncError(f"Expected exactly one YAML definition in {folder}")
        source = definitions[0]
        try:
            content = snapshot.read(source, 1_000_000).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SyncError(f"Workflow definition must be UTF-8: {source}") from exc
        value = document(content)
        name = value.get("name")
        if (
            not isinstance(name, str)
            or name != name.strip()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,99}", name)
        ):
            raise SyncError(f"Workflow needs a valid, stable name (1–100 characters): {source}")
        if name in names:
            raise SyncError(f"Duplicate workflow name: {name}")
        names.add(name)
        files = {}
        total = 0
        for filename in sorted(snapshot.entries):
            sibling = PurePosixPath(filename)
            if sibling.parent != folder or sibling.suffix.lower() != ".md":
                continue
            data = snapshot.read(filename, 2 * 1024 * 1024)
            total += len(data)
            files[f".outcomeci/instructions/{sibling.name}"] = base64.b64encode(data).decode()
        if len(files) > 200 or total > 20 * 1024 * 1024:
            raise SyncError(f"Instruction bundle exceeds the API limits: {source}")
        bundle = Bundle(name, source, content, files, value)
        validate(bundle)
        bundles.append(bundle)
    return bundles


def validate(bundle: Bundle) -> None:
    def check_reasons(value):
        if isinstance(value, dict):
            reason = value.get("reason")
            if (
                isinstance(reason, str)
                and "\n" not in reason.strip()
                and reason.strip().endswith(".md")
            ):
                filename = reason.strip()
                if (
                    "/" in filename
                    or "\\" in filename
                    or f".outcomeci/instructions/{filename}" not in bundle.files
                ):
                    raise SyncError(
                        f"Instruction references must name a sibling Markdown file: {bundle.path}"
                    )
            for child in value.values():
                check_reasons(child)
        elif isinstance(value, list):
            for child in value:
                check_reasons(child)

    check_reasons(bundle.definition)
    with tempfile.TemporaryDirectory(prefix="outcomeci-sync-validate-") as temporary:
        root = Path(temporary)
        source = root / bundle.filename
        source.write_text(bundle.content, encoding="utf-8")
        for name, encoded in bundle.files.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base64.b64decode(encoded))
        try:
            compile_workflow(source)
        except Exception as exc:
            # Compiler messages may contain user-controlled workflow source.
            # The path identifies the bundle; CLI compile provides local detail.
            raise SyncError(
                f"Workflow compilation failed: {bundle.path} ({type(exc).__name__})"
            ) from exc
