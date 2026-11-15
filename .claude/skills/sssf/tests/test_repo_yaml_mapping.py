"""repo.yaml: a top level that is not a mapping refuses; empty and mappings load as before.
The config comes from the sssf.config.yaml SSSF ships, never a test-only one."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))
from adw_modules import agents, agentsh_guard, repo_settings  # noqa: E402
from adw_modules.data_types import LocalGuard  # noqa: E402

SHIPPED = SKILL / "templates" / "sssf.config.yaml"
HUB_REPO_YAML = SKILL.parents[2] / "adws/adw_sssf_config/repo.yaml"


@pytest.fixture
def cfg():
    return agents.load_config(str(SHIPPED))


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _repo_with(tmp_path, body: str | None, name="repo") -> Path:
    repo = _git_repo(tmp_path / name)
    if body is not None:
        (repo / "adws/adw_sssf_config").mkdir(parents=True)
        (repo / "adws/adw_sssf_config/repo.yaml").write_text(body, encoding="utf-8")
    return repo


REFUSED = [
    ("[]\n", "list"), ("false\n", "bool"), ("0\n", "int"), ("''\n", "str"),
    ('"agentsh"\n', "str"), ("[local_guard: agentsh]\n", "list"), ("1\n", "int"),
    ("true\n", "bool"), ("agentsh\n", "str"), ('""\n', "str"), ("0.0\n", "float"),
    ("no\n", "bool"), ("- local_guard: agentsh\n", "list"), ("!!set {}\n", "set"),
    ('!!binary ""\n', "bytes"), ("2026-11-15\n", "date"),
]


@pytest.mark.parametrize("body,type_name", REFUSED, ids=[repr(b) for b, _ in REFUSED])
def test_a_non_mapping_refuses(tmp_path, monkeypatch, cfg, body, type_name):
    repo = _repo_with(tmp_path, body)
    with pytest.raises(ValueError) as exc:
        repo_settings.load(repo)
    message = str(exc.value)
    assert str(repo / repo_settings.PATH) in message
    assert "mapping" in message
    assert type_name in message
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as stop:
        agentsh_guard.read(cfg)
    assert "repo.yaml" in str(stop.value) and "mapping" in str(stop.value)


EMPTY = [None, "", "\n", "# only a comment\n", "null\n", "~\n", "Null\n", "NULL\n",
         "---\n", "--- ~\n", "﻿", "{}\n"]


@pytest.mark.parametrize("body", EMPTY, ids=[repr(b) for b in EMPTY])
def test_an_empty_document_gives_the_defaults(tmp_path, monkeypatch, cfg, body):
    repo = _repo_with(tmp_path, body)
    assert repo_settings.load(repo) == repo_settings.RepoSettings()
    monkeypatch.chdir(repo)
    assert agentsh_guard.read(cfg) == LocalGuard(mode="none")


MAPPINGS = [
    "local_guard: agentsh\n", "local_guard: none\n", "local_guard: text\n", "deny_exec: []\n",
    "local_guard: agentsh\nlocal_guard: none\n", "local_guard: none\nlocal_guard: agentsh\n",
    "local_guard: !!str none\n", "local_guard: none\nsandbox: vercel\n",
    "merge: ff_only\nmax_worktree_runs: 1\n",
    (SKILL / "templates" / "repo.yaml").read_text(encoding="utf-8"),
]
if HUB_REPO_YAML.is_file():
    MAPPINGS.append(HUB_REPO_YAML.read_text(encoding="utf-8"))


@pytest.mark.parametrize("body", MAPPINGS, ids=[f"m{i}" for i in range(len(MAPPINGS))])
def test_a_mapping_loads_as_before(tmp_path, body):
    repo = _repo_with(tmp_path, body)
    assert repo_settings.load(repo) == repo_settings.RepoSettings.model_validate(yaml.safe_load(body))


def test_local_guard_agentsh_loads(tmp_path):
    repo = _repo_with(tmp_path, "local_guard: agentsh\n")
    assert repo_settings.load(repo).local_guard == "agentsh"


@pytest.mark.parametrize("body", [
    "locl_guard: agentsh\n", "local_guard: None\n", "1: 2\n",
    "local_guard: agentsh\nsandbox: vercel\n",
])
def test_a_bad_mapping_keeps_its_error(tmp_path, body):
    repo = _repo_with(tmp_path, body)
    with pytest.raises(ValidationError):
        repo_settings.load(repo)


def test_two_documents_stay_a_yaml_error(tmp_path):
    repo = _repo_with(tmp_path, "a: 1\n---\nb: 2\n")
    with pytest.raises(yaml.YAMLError):
        repo_settings.load(repo)


def test_a_file_rewritten_as_a_list_stops_the_load(tmp_path):
    repo = _repo_with(tmp_path, "local_guard: agentsh\n")
    assert repo_settings.load(repo).local_guard == "agentsh"
    (repo / repo_settings.PATH).write_text("[]\n")
    with pytest.raises(ValueError):
        repo_settings.load(repo)
