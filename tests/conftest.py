import shutil

import pytest

from fillmate.pack import load_pack
from fillmate.paths import examples_dir


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/data folders; nothing touches the real user's files."""
    monkeypatch.setenv("FILLMATE_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def example_dir(tmp_path):
    dest = tmp_path / "travel-request"
    shutil.copytree(examples_dir() / "travel-request", dest)
    return dest


@pytest.fixture
def example(example_dir):
    return load_pack(example_dir)


@pytest.fixture
def profile():
    return {"full_name": "Jane Doe", "employee_id": "42", "department": "R&D",
            "manager_email": "boss@example.com"}


class FakeSender:
    """Records messages instead of sending them."""

    name = "fake"

    def __init__(self):
        self.sent = []

    def from_address(self):
        return "me@example.com"

    def ready(self):
        return True, "me@example.com"

    def send(self, msg):
        self.sent.append(msg)
        return "fake-id"


@pytest.fixture
def fake_sender():
    return FakeSender()
