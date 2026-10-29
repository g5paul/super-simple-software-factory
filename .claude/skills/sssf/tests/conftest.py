"""Shared test setup.

install.py records every repo it installs into in a registry file under the
user's config dir. Point it at a temp file for every test, so the suite never
writes to the real ~/.config/sssf/projects.json. monkeypatch changes
os.environ, so install.py subprocesses inherit the setting.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def private_registry(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("registry") / "projects.json"
    monkeypatch.setenv("SSSF_REGISTRY", str(path))
    return path
