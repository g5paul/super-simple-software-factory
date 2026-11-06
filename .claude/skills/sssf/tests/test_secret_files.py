"""Keeping a repo's secret files out of agents' reach.

Settings and the roster come from the files SSSF ships under templates/.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates"
sys.path.insert(0, str(TEMPLATES / "adws"))

from adw_modules import agent_cc, agent_pi, agents, rules, secret_files, utils
from adw_modules.data_types import AgentRequest, SSSFConfig
from adw_modules.repo_settings import RepoSettings

REFUSE_SHELL = agent_cc.REFUSE_SHELL


def shipped_settings(**extra) -> RepoSettings:
    data = yaml.safe_load((TEMPLATES / "repo.yaml").read_text()) or {}
    return RepoSettings.model_validate({**data, **extra})


def shipped_config() -> SSSFConfig:
    return SSSFConfig.model_validate(yaml.safe_load((TEMPLATES / "sssf.config.yaml").read_text()))


def make_repo(root: Path) -> Path:
    for name in (".env", ".env.sample", ".env.example", "sub/.env.local",
                 "node_modules/x/.env", "adws/adw_data/worktrees/a/.env",
                 "config/creds.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("K=v\n")
    return root


def find(root: Path, **kw) -> list[str]:
    settings = shipped_settings(secret_files=["config/*.json"])
    return secret_files.find([root], settings.secret_files,
                             skip=[root / "adws/adw_data"], **kw)


def test_find_returns_the_default_set_and_pattern_matches(tmp_path):
    make_repo(tmp_path)
    assert find(tmp_path) == sorted(str(tmp_path / n) for n in
                                    (".env", "sub/.env.local", "config/creds.json"))


def test_a_worktree_root_outside_the_main_root_is_walked_too(tmp_path):
    main, tree = make_repo(tmp_path / "main"), tmp_path / "wt"
    (tree / "deep").mkdir(parents=True)
    (tree / "deep" / ".env.production").write_text("K=v\n")
    got = secret_files.find([main, tree], [], skip=[main / "adws/adw_data"])
    assert str(tree / "deep" / ".env.production") in got and str(main / ".env") in got


def test_a_pattern_without_a_slash_matches_a_file_name_anywhere(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "k.pem").write_text("x")
    assert secret_files.find([tmp_path], ["*.pem"]) == [str(tmp_path / "a/b/k.pem")]


def test_agents_get_no_env_key_the_dotenv_set_but_checks_keep_them(monkeypatch):
    keys = {"APP_SECRET", "SSSF_X", "OBS_Y", "DATABASE_URL", "FIREWORKS_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN"}
    monkeypatch.setattr(utils, "DOTENV_KEYS", frozenset(keys))
    for key in keys | {"SHELL_ONLY"}:
        monkeypatch.setenv(key, "v")
    cc = agent_cc.child_env(allowed=["DATABASE_URL"])
    assert "APP_SECRET" not in cc and "FIREWORKS_API_KEY" not in cc
    assert {"SSSF_X", "OBS_Y", "DATABASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "SHELL_ONLY"} <= set(cc)
    pi = agent_pi.child_env(allowed=["DATABASE_URL"])
    assert "FIREWORKS_API_KEY" in pi and "APP_SECRET" not in pi
    assert "APP_SECRET" in utils.operator_env()


def request_with(files: list[str]) -> AgentRequest:
    return AgentRequest(prompt="go", system_prompt="", model="anthropic/claude-sonnet-5-5",
                        session_id="f5e2a3a8-0d8e-4b8a-9a43-6c1f3e9b2d10",
                        session_dir="s", raw_output_path="r", secret_files=files)


def test_claude_code_denies_read_of_each_file_once_with_the_double_slash_form(tmp_path):
    make_repo(tmp_path)
    files = find(tmp_path)
    cmd = agent_cc._command(request_with(files), resume=False)
    values = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--disallowedTools"]
    assert values == [f"Read(/{f})" for f in files] and len(values) == 3
    assert all(v.startswith("Read(//") and "*" not in v and ".env.sample" not in v for v in values)


def refuse(line: str, files: list[str], cwd: Path):
    env = {"PATH": "/usr/bin:/bin",
           "SSSF_REFUSE_RULES": json.dumps(rules.refusing([secret_files.shell_rule(files)]))}
    return subprocess.run([sys.executable, str(REFUSE_SHELL), line], capture_output=True,
                          text=True, env=env, cwd=cwd)


def test_the_shell_rule_refuses_commands_naming_a_secret_file(tmp_path):
    make_repo(tmp_path)
    files = find(tmp_path)
    for line in ("cat .env", "source ./sub/.env.local"):
        done = refuse(line, files, tmp_path)
        assert done.returncode == 126 and done.stderr.startswith(rules.REFUSED), line
    for line in ("cat .env.sample", "echo env"):
        assert refuse(line, files, tmp_path).returncode == 0, line
    pattern = secret_files.shell_rule(files).forbid[0]
    assert not re.search(pattern, "cat .env.example", re.M)
    assert re.search(pattern, "grep KEY sub/.env", re.M)
    assert re.search(pattern, "cd /x && eval 'cat .env' < /dev/null && pwd -P >| /tmp/c", re.M)


def test_secret_guard_finds_files_and_builds_the_rule(tmp_path, monkeypatch):
    cfg = shipped_config()
    has = make_repo(tmp_path / "has")
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(has)
    files, rule = agents._secret_guard(SimpleNamespace(repo_root=has, cfg=cfg))
    assert files == [str(has / ".env"), str(has / "sub/.env.local")]
    assert rule is not None and rule.on_match == "refuse"
    monkeypatch.chdir(bare)
    assert agents._secret_guard(SimpleNamespace(repo_root=bare, cfg=cfg)) == ([], None)


def test_the_refuse_extension_is_the_last_pi_extension_without_refuse_rules(tmp_path, monkeypatch):
    seen = tmp_path / "seen"
    script = tmp_path / "pi"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {seen}\n')
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    req = request_with([]).model_copy(update={"extensions": ["first.ts"], "cwd": str(tmp_path)})
    try:
        agent_pi.run(req)
    except RuntimeError:
        pass
    argv = seen.read_text().splitlines()
    loaded = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert loaded == ["first.ts", str(agent_pi.REFUSE_EXTENSION)]
