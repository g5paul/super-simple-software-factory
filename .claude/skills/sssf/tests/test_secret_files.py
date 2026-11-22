"""Keeping a repo's secret files out of agents' reach.

Settings and the roster come from the files SSSF ships under templates/.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates"
sys.path.insert(0, str(TEMPLATES / "adws"))

from adw_modules import agent_cc, agent_pi, agents, repo_settings, rules, sandbox, secret_files, utils
from adw_modules.data_types import AgentRequest, SSSFConfig, LocalGuard
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


def git_init(root: Path) -> Path:
    """Make `root` a git repo with one commit: SSSF's snapshot fails closed
    outside one, so a test that runs agents.execute needs a real repo."""
    import os, subprocess
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    for args in (["init", "-qb", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return root


def find(root: Path, **kw) -> list[str]:
    settings = shipped_settings(secret_files=["config/*.json"])
    return secret_files.find([root], settings.secret_files,
                             skip=[root / "adws/adw_data"], **kw)


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
def test_find_returns_the_default_set_and_pattern_matches(tmp_path):
    make_repo(tmp_path)
    assert find(tmp_path) == sorted(str(tmp_path / n) for n in
                                    (".env", "sub/.env.local", "config/creds.json"))


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
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


def test_the_container_login_check_reads_the_dir_the_pi_agent_gets(tmp_path, monkeypatch):
    home, clean = tmp_path / "home", tmp_path / "clean"
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/auth.json").write_text('{"openai": {"type": "oauth"}}')
    clean.mkdir()
    monkeypatch.setattr(utils, "DOTENV_KEYS", frozenset({"PI_CODING_AGENT_DIR"}))
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", None)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(clean))
    monkeypatch.setenv(agent_pi.CONTAINER_SOCKET_VAR, str(tmp_path / "broker.sock"))
    env = agent_pi.child_env()
    assert "PI_CODING_AGENT_DIR" not in env
    with pytest.raises(sandbox.SandboxFailed, match="ChatGPT login"):
        agent_pi._in_container(env, sandbox.LOCAL)


def request_with(files: list[str], tmp: Path) -> AgentRequest:
    # Paths under tmp: a bare "r" here once wrote a file named r into
    # whatever folder pytest ran in, and two guard builds committed it.
    return AgentRequest(prompt="go", system_prompt="", model="anthropic/claude-sonnet-5-5",
                        session_id="f5e2a3a8-0d8e-4b8a-9a43-6c1f3e9b2d10",
                        session_dir=str(tmp / "s"), raw_output_path=str(tmp / "r"), data_root=str(tmp),
                        secret_files=files, local_guard=LocalGuard(mode="none"))


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
def test_claude_code_denies_read_of_each_file_once_with_the_double_slash_form(tmp_path):
    make_repo(tmp_path)
    files = find(tmp_path)
    cmd = agent_cc._command(request_with(files, tmp_path), resume=False)
    values = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--disallowedTools"]
    assert values == [agent_cc.deny_rule(f) for f in files] and len(values) == 3
    assert all(v.startswith("Read(//") and "*" not in v and ".env.sample" not in v for v in values)


def test_deny_rule_escapes_gitignore_pattern_characters():
    assert agent_cc.deny_rule("/abs/weird [x] dir/.env") == r"Read(//abs/weird\ \[x\]\ dir/.env)"
    assert agent_cc.deny_rule("/a/*?!\\b/.env") == r"Read(//a/\*\?\!\\b/.env)"
    assert agent_cc.deny_rule("/home/u/repo/sub/.env.local") == "Read(//home/u/repo/sub/.env.local)"


def test_command_denies_a_weird_and_an_ordinary_path_one_rule_each(tmp_path):
    p1, p2 = "/abs/weird [x] dir/.env", "/home/u/repo/.env"
    cmd = agent_cc._command(request_with([p1, p2], tmp_path), resume=False)
    values = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--disallowedTools"]
    assert values == [agent_cc.deny_rule(p1), agent_cc.deny_rule(p2)]


def stamped_repo(tmp: Path) -> Path:
    """A git repo stamped by the shipped installer: default roster, shipped config."""
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    # SSSF refuses an agent in a repo with no commit, so give the repo a first one.
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
    done =subprocess.run([sys.executable, str(SKILL / "scripts" / "install.py")], cwd=tmp,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    return tmp


PROBE = '''\
import json, sys
sys.path.insert(0, "adws")
from adw_modules import utils, agent_cc, agent_pi
print(json.dumps({
    "dotenv": sorted(utils.DOTENV_KEYS),
    "cc": sorted(agent_cc.child_env()),
    "pi": sorted(agent_pi.child_env()),
    "operator": sorted(utils.operator_env()),
}))
'''


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
def test_a_key_the_environment_already_had_is_still_held_back_from_agents(tmp_path):
    stamped_repo(tmp_path)
    (tmp_path / ".env").write_text("APP_SECRET=from-dotenv\nSSSF_X=1\nDATABASE_URL=db\n")
    (tmp_path / "probe.py").write_text(PROBE)
    env = {k: v for k, v in os.environ.items()
           if k not in ("APP_SECRET", "SSSF_X", "DATABASE_URL")}
    env.update(APP_SECRET="from-shell", SHELL_ONLY="1")
    done = subprocess.run([sys.executable, "probe.py"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert "APP_SECRET" in got["dotenv"]
    for agent in ("cc", "pi"):
        assert "APP_SECRET" not in got[agent] and "DATABASE_URL" not in got[agent]
        assert "SSSF_X" in got[agent] and "SHELL_ONLY" in got[agent]
    assert "APP_SECRET" in got["operator"]


def dotenv_repo(tmp_path: Path) -> Path:
    stamped_repo(tmp_path)
    (tmp_path / ".env").write_text("APP_SECRET=from-dotenv\nSSSF_X=1\nDATABASE_URL=db\n")
    (tmp_path / "probe.py").write_text(PROBE)
    return tmp_path


def run_probe(cwd: Path, sign: str | None = None, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("APP_SECRET", "SSSF_X", "DATABASE_URL", "SSSF_AGENTSH", "SSSF_AGENT")}
    env["SHELL_ONLY"] = "1"
    if sign is not None:
        env["SSSF_AGENTSH"] = sign
    env.update(extra)
    return subprocess.run([sys.executable, "probe.py"], cwd=cwd, env=env,
                          capture_output=True, text=True, check=False)


def got(done) -> dict:
    return json.loads(done.stdout.strip().splitlines()[-1])


def no_acl(path: Path) -> None:
    """Take every permission away with an ACL, with no chmod."""
    if not hasattr(os, "setxattr"):
        pytest.skip("no os.setxattr")
    acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, 0, 0xFFFFFFFF) for tag in (0x01, 0x04, 0x20))
    try:
        os.setxattr(path, "system.posix_acl_access", acl)
    except OSError:
        pytest.skip("filesystem without ACLs")
    try:
        path.open().close()
    except PermissionError:
        return
    pytest.skip("the ACL did not make the file unreadable")


def make_unreadable(path: Path, how: str) -> None:
    if how == "chmod":
        path.chmod(0o000)
    else:
        no_acl(path)


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")
@pytest.mark.parametrize("how", ["chmod", "acl"])
def test_an_agents_process_under_agentsh_imports_utils_past_an_unreadable_env_file(tmp_path, how):
    repo = dotenv_repo(tmp_path)
    try:
        make_unreadable(repo / ".env", how)
        done = run_probe(repo, sign="1")
    finally:
        (repo / ".env").chmod(0o600)
    assert done.returncode == 0, done.stderr
    out = got(done)
    assert out["dotenv"] == []
    for agent in ("operator", "cc", "pi"):
        assert "APP_SECRET" not in out[agent] and "DATABASE_URL" not in out[agent]
    for agent in ("cc", "pi"):
        assert "SHELL_ONLY" in out[agent]


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")
@pytest.mark.parametrize("how", ["chmod", "acl"])
@pytest.mark.parametrize("sign,extra", [
    (None, {}), ("0", {}), ("", {}), ("true", {}), (None, {"SSSF_AGENT": "builder"}),
])
def test_any_other_process_stops_on_an_unreadable_env_file(tmp_path, how, sign, extra):
    repo = dotenv_repo(tmp_path)
    try:
        make_unreadable(repo / ".env", how)
        done = run_probe(repo, sign=sign, APP_SECRET="from-shell", **extra)
    finally:
        (repo / ".env").chmod(0o600)
    assert done.returncode != 0
    assert "PermissionError" in done.stderr
    assert done.stdout.strip() == ""


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")
def test_an_env_file_cannot_give_a_process_the_sign(tmp_path):
    repo = dotenv_repo(tmp_path)
    with (repo / ".env").open("a") as f:
        f.write("SSSF_AGENTSH=1\n")
    early = repo / "adws" / ".env"
    early.write_text("X=1\n")
    try:
        early.chmod(0o000)
        done = run_probe(repo)
    finally:
        early.chmod(0o600)
    assert done.returncode != 0
    assert "PermissionError" in done.stderr
    assert done.stdout.strip() == ""


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
@pytest.mark.parametrize("sign", [None, "1"])
def test_a_readable_env_file_gives_the_same_keys_from_the_checkout_and_a_worktree_folder(tmp_path, sign):
    repo = dotenv_repo(tmp_path)
    w1 = repo / "adws" / "adw_data" / "worktrees" / "w1"
    shutil.copytree(repo / "adws" / "adw_modules", w1 / "adws" / "adw_modules")
    (w1 / "probe.py").write_text(PROBE)
    for cwd in (repo, w1):
        done = run_probe(cwd, sign=sign)
        assert done.returncode == 0, done.stderr
        out = got(done)
        assert out["dotenv"] == ["APP_SECRET", "DATABASE_URL", "SSSF_X"]
        for name in ("APP_SECRET", "DATABASE_URL", "SSSF_X"):
            assert name in out["operator"]
        for agent in ("cc", "pi"):
            assert "APP_SECRET" not in out[agent] and "DATABASE_URL" not in out[agent]
            assert "SSSF_X" in out[agent] and "SHELL_ONLY" in out[agent]


FAKE_PI ='''\
#!/usr/bin/env python3
import json, os, sys
if "--list-models" in sys.argv:
    print("provider model context")
    print("google gemini-3.6-flash 1.0M")
    sys.exit(0)
if "-p" in sys.argv:
    with open(os.environ["FAKE_PI_ENV"], "w") as f:
        f.write(json.dumps(sorted(os.environ)))
    sys.exit(1)
'''


@pytest.mark.skipif(not (shutil.which("just") and shutil.which("uv")),
                    reason="needs just and uv on PATH")
# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
def test_a_launch_through_the_stamped_just_recipe_keeps_dotenv_keys_from_the_agent(tmp_path):
    (tmp_path / "repo").mkdir()
    repo = stamped_repo(tmp_path / "repo")
    (repo / ".env").write_text("APP_SECRET=from-dotenv\n")
    fake = tmp_path / "bin" / "pi"
    fake.parent.mkdir()
    fake.write_text(FAKE_PI)
    fake.chmod(0o755)
    dump = tmp_path / "dump.json"
    env = {k: v for k, v in os.environ.items() if k != "APP_SECRET"}
    env.update(PI_PATH=str(fake), FAKE_PI_ENV=str(dump))
    subprocess.run(["just", "prompt", "say hi"], cwd=repo, env=env, capture_output=True,
                   text=True, timeout=300, check=False)
    assert dump.exists()
    keys = json.loads(dump.read_text())
    assert "SSSF_ADW_ID" in keys and "APP_SECRET" not in keys


def refuse(line: str, files: list[str], cwd: Path):
    env = {"PATH": "/usr/bin:/bin",
           "SSSF_REFUSE_RULES": json.dumps(rules.refusing([secret_files.shell_rule(files)]))}
    return subprocess.run([sys.executable, str(REFUSE_SHELL), line], capture_output=True,
                          text=True, env=env, cwd=cwd)


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
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


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
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
    req = request_with([], tmp_path).model_copy(update={"extensions": ["first.ts"], "cwd": str(tmp_path)})
    try:
        agent_pi.run(req)
    except RuntimeError:
        pass
    argv = seen.read_text().splitlines()
    loaded = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert loaded == ["first.ts", str(agent_pi.REFUSE_EXTENSION)]


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
def test_execute_marks_only_the_built_in_rule_in_the_requests_refuse_rules(tmp_path, monkeypatch):
    cfg = shipped_config()
    root = git_init(make_repo(tmp_path / "repo"))
    monkeypatch.chdir(root)
    push = {"rule": "Never run `git push`.", "forbid": [r"\bgit\s+push\b"], "on_match": "refuse"}
    twin = {"rule": secret_files.RULE_TEXT, "forbid": [r"\bcat\s+\.env\b"], "on_match": "refuse"}
    rm = {"rule": "Never run rm -rf.", "forbid": [r"\brm\s+-rf\b"]}
    data = yaml.safe_load((TEMPLATES / "repo.yaml").read_text()) or {}
    data["agent_rules"] = [push, twin, rm]
    path = root / repo_settings.PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    for a in cfg.agents:
        for ref in (a.prompt_engineering.system, a.prompt_engineering.user):
            f = root / ref
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch()

    class Captured(Exception):
        pass

    seen = []

    def run_fake(request, **kw):
        seen.append(request)
        raise Captured()

    fake = SimpleNamespace(run=run_fake, ToolCallTracker=agent_pi.ToolCallTracker)
    monkeypatch.setattr(agents, "_interface", lambda agent: fake)
    for name in ("check_run", "check_live", "check_daily", "check_machine"):
        monkeypatch.setattr(agents.budget, name, lambda *a, **k: None)
    monkeypatch.setattr(agents.permissions, "guard_git_dir", lambda run: None)
    monkeypatch.setattr(agents.permissions, "restore_git_dir", lambda *a, **k: [])
    monkeypatch.setattr(agents.agent_cc, "check_login", lambda *a, **k: None)
    monkeypatch.setattr(agents.account, "check", lambda *a, **k: None)
    run = SimpleNamespace(
        cfg=cfg, adw_id="r1", session_dir=tmp_path / "s", data_root=tmp_path, repo_root=root,
        context_handoff_dir=tmp_path / "h", agent_map={},
        tracer=SimpleNamespace(event=lambda e: None, process_start=lambda *a: None,
                               process_end=lambda *a: None, gate_row=lambda *a: None,
                               agent_session_row=lambda *a, **k: None),
        console=SimpleNamespace(agent_started=lambda *a: None, advise=lambda *a: None,
                                note=lambda *a: None, envelope_summary=lambda *a: None),
        add_usage=lambda *a: None, save_agent_map=lambda *a: None)
    phase = SimpleNamespace(phase_id="r1_01", params=SimpleNamespace(owner="builder", name="build"))
    call = SimpleNamespace(prompt="x", previous=None, session=None)
    with pytest.raises(Captured):
        agents.execute(run, phase, call)

    [request] = seen
    got = request.refuse_rules
    find_files = [str(root / ".env"), str(root / "sub/.env.local")]
    assert [r for r in got if r.get("builtin")] == [
        {"rule": secret_files.RULE_TEXT, "forbid": secret_files.shell_rule(find_files).forbid,
         "builtin": True}]
    assert {"rule": push["rule"], "forbid": push["forbid"]} in got
    assert {"rule": twin["rule"], "forbid": twin["forbid"]} in got
    assert not any(r["rule"] == rm["rule"] for r in got)
