"""local_guard: `none` is the no-layer value; `text` is its old name and still loads.
The config comes from the sssf.config.yaml SSSF ships, never a test-only one."""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))
from adw_modules import agent_cc, agent_pi, agents, agentsh_guard, repo_settings, sandbox  # noqa: E402
from adw_modules.data_types import LocalGuard  # noqa: E402

sys.path.insert(0, str(SKILL / "tests"))
from test_agent_cc import fake, request  # noqa: E402,F401  (fixture and helper)
from test_agent_pi import _fake_pi, _turn  # noqa: E402

SHIPPED = SKILL / "templates" / "sssf.config.yaml"
ADWS = SKILL / "templates" / "adws"
FIXTURES = SKILL / "tests" / "fixtures"


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def cfg():
    return agents.load_config(str(SHIPPED))


def _repo_with(tmp_path, body: str, name="repo") -> Path:
    repo = git_repo(tmp_path / name)
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text(body)
    return repo


def _bodies():
    # text, none, and no key at all
    return ["local_guard: text\n", "local_guard: none\n", "deny_exec: []\n"]


def test_the_names_load_alike(tmp_path, monkeypatch, cfg):
    settings, guards = [], []
    for i, body in enumerate(_bodies()):
        repo = _repo_with(tmp_path, body, f"r{i}")
        monkeypatch.chdir(repo)
        settings.append(repo_settings.load(repo))
        guards.append(agentsh_guard.read(cfg))
    assert settings[0] == settings[1] == settings[2]
    assert all(s.local_guard == "none" for s in settings)
    assert guards[0] == guards[1] == guards[2] == LocalGuard(mode="none")


def test_the_names_launch_alike(tmp_path, monkeypatch, cfg, fake):
    # Run from a clean environment: this suite may itself run under agentsh, which sets these.
    for key in [k for k in os.environ if k.startswith("AGENTSH_")] + ["SSSF_AGENTSH", "CLAUDE_CONFIG_DIR"]:
        monkeypatch.delenv(key, raising=False)
    calls = []
    real = sandbox.LOCAL.start

    def record(command, *, cwd=None, env=None):
        calls.append((list(command), dict(env or {})))
        return real(command, cwd=cwd, env=env)

    monkeypatch.setattr(sandbox.LOCAL, "start", record)
    monkeypatch.setattr(agentsh_guard, "check_machine",
                        lambda *a, **k: pytest.fail("check_machine ran under no layer"))
    fake(FIXTURES / "cc_turn1.jsonl")
    cc_base = request(tmp_path)
    pi_base = _fake_pi(tmp_path, monkeypatch, _turn("stop", "hi"))
    seen = {"cc": [], "pi": []}
    for i, body in enumerate(_bodies()):
        repo = _repo_with(tmp_path, body, f"r{i}")
        monkeypatch.chdir(repo)
        guard = agentsh_guard.read(cfg)
        for kind, module, base in (("cc", agent_cc, cc_base), ("pi", agent_pi, pi_base)):
            # A second run on the same session id would resume it; start each from a clean session.
            shutil.rmtree(base.session_dir, ignore_errors=True)
            before = len(calls)
            module.run(base.model_copy(update={"local_guard": guard}))
            assert len(calls) == before + 1
            seen[kind].append(calls[-1])
    for kind, runs in seen.items():
        (cmd0, env0), (cmd1, env1), (cmd2, env2) = runs
        assert cmd0 == cmd1 == cmd2 and env0 == env1 == env2
        assert Path(cmd0[0]).name != agentsh_guard.BINARY
        assert cmd0[0] != agentsh_guard.BINARY
        for key in env0:
            assert not key.startswith("AGENTSH_")
            assert key not in ("SSSF_AGENTSH", "CLAUDE_CONFIG_DIR")


@pytest.mark.parametrize("body", [
    "local_guard: None\n", "local_guard: TEXT\n", "local_guard: Agentsh\n", "local_guard: off\n",
    "local_guard: false\n", "local_guard: null\n", "local_guard: ~\n", "local_guard:\n",
    "local_guard: ''\n", "local_guard: [none]\n", "local_guard: [text]\n"])
def test_other_values_fail_closed_at_load(tmp_path, monkeypatch, cfg, body):
    repo = _repo_with(tmp_path, body)
    with pytest.raises(ValidationError) as error:
        repo_settings.load(repo)
    assert "none" in str(error.value) and "agentsh" in str(error.value)
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as exit_:
        agentsh_guard.read(cfg)
    assert "repo.yaml" in str(exit_.value)


@pytest.mark.parametrize("mode", ["text", "None", "", None])
def test_nothing_else_reaches_local_guard(mode):
    with pytest.raises(ValidationError):
        LocalGuard(mode=mode)


def test_agentsh_still_loads_and_still_refuses_a_vm(tmp_path):
    repo = _repo_with(tmp_path, "local_guard: agentsh\n")
    assert repo_settings.load(repo).local_guard == "agentsh"
    repo = _repo_with(tmp_path, "local_guard: agentsh\nsandbox: vercel\n", "vm")
    with pytest.raises(ValidationError):
        repo_settings.load(repo)


def _stale(source: str) -> list[str]:
    """Places that compare a guard mode with "text", pass mode="text", or list "text" in a Literal."""
    def is_text(node):
        return isinstance(node, ast.Constant) and node.value == "text"

    def holds_text(node):
        if is_text(node):
            return True
        return isinstance(node, (ast.Tuple, ast.List, ast.Set)) and any(is_text(e) for e in node.elts)

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(holds_text(o) for o in operands):
                if not any('"type"' in ast.unparse(o) or "'type'" in ast.unparse(o) for o in operands):
                    found.append(ast.unparse(node))
        elif isinstance(node, ast.keyword) and node.arg == "mode" and is_text(node.value):
            found.append("mode='text'")
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("Literal"):
            if any(is_text(n) for n in ast.walk(node.slice)):
                found.append(ast.unparse(node))
    return found


def test_no_stale_text_mode_in_the_modules():
    for sample in ('guard.mode == "text"', 'LocalGuard(mode="text")', 'mode: Literal["text", "agentsh"]'):
        assert _stale(sample), sample
    assert not _stale('part.get("type") == "text"')
    hits = {}
    for path in sorted(ADWS.rglob("*.py")):
        found = _stale(path.read_text())
        if found:
            hits[path.name] = found
    assert len(hits.pop("repo_settings.py", [])) == 1, "repo_settings.py holds exactly the alias"
    assert hits == {}
