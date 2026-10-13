import base64
import subprocess

import pytest

from outcomeci_sync.package import Bundle, document


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("config", "commit.gpgsign", "false")

    def commit(files):
        for name, content in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        git("add", ".")
        git("commit", "--allow-empty", "-m", "fixture")
        return tmp_path

    return commit


@pytest.fixture
def bundle():
    def make(name="hello", instruction="Say hello.", path=None, dispatcher=None, targets=()):
        trigger = f"{{type: dispatcher, dispatcher: {dispatcher}}}" if dispatcher else "manual"
        content = f"apiVersion: outcomeci.workflow/v1\nname: {name}\ntrigger: {trigger}\n"
        if targets:
            content += "type: dispatcher\nsteps:\n"
            for index, target in enumerate(targets):
                content += f"  - route{index}:\n      dispatch: {target}\n      with: trigger\n"
        else:
            content += "steps:\n  - greet:\n      reason: greet.md\n"
        return Bundle(
            name,
            path or f".outcomeci/workflows/{name}/workflow.yml",
            content,
            {".outcomeci/instructions/greet.md": base64.b64encode(instruction.encode()).decode()},
            document(content),
        )

    return make
