"""adw_modules/sandbox_openshell.py: `--sandbox openshell` drives the `openshell`
CLI. A fake one, first on PATH, stands in; no gateway, sandbox or Podman runs.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, repo_settings, sandbox, sandbox_openshell as osh  # noqa: E402
from adw_modules.sandbox_remote import Remote  # noqa: E402

FAKE = '''#!{python}
import base64, json, os, subprocess, sys, time
argv = sys.argv[1:]
data = sys.stdin.buffer.read() if argv[:2] == ["sandbox", "exec"] else b""
with open(os.environ["FAKE_OPENSHELL_LOG"], "a") as f:
    f.write(json.dumps({{"argv": argv, "stdin": base64.b64encode(data).decode()}}) + "\\n")
code = int(os.environ.get("FAKE_OPENSHELL_CODE", "0"))
if argv[:2] == ["sandbox", "exec"]:
    if os.environ.get("FAKE_OPENSHELL_MODE") == "down":
        sys.stderr.write("error: gateway unreachable\\n")
        sys.exit(1)
    cmd = argv[argv.index("--") + 1:]
    cwd = argv[argv.index("--workdir") + 1] if "--workdir" in argv else None
    sys.exit(subprocess.run(cmd, input=data, cwd=cwd).returncode)
if argv[:2] == ["sandbox", "list"]:
    print(os.environ.get("FAKE_OPENSHELL_NAMES", ""))
if argv[:1] == ["logs"]:
    path = os.environ.get("FAKE_OPENSHELL_LOGS")
    if not path:
        sys.stderr.write("no logs\\n")
        sys.exit(1)
    sys.stdout.write(open(path).read())
sys.exit(code)
'''


@pytest.fixture
def cli(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake = bin_ / "openshell"
    fake.write_text(FAKE.format(python=sys.executable))
    fake.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_OPENSHELL_LOG", str(log))
    monkeypatch.setenv("FAKE_OPENSHELL_MODE", "local")

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return calls


def stdin_of(call) -> str:
    return base64.b64decode(call["stdin"]).decode()


TOKEN = "sk-ant-oat-SECRET value"


def test_no_env_value_reaches_a_command_line(cli):
    vm = osh._VM("sb")
    env = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "OTHER": "other-value-1"}
    vm.run_process("true", env=env)
    proc = vm.create_process("true", env={"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
    proc.wait()
    for call in cli():
        text = json.dumps(call["argv"])
        assert TOKEN not in text and "other-value-1" not in text
    sent = [stdin_of(c) for c in cli()]
    assert any(base64.b64encode(TOKEN.encode()).decode() in s for s in sent)
    assert len(sent) == 2 and all(TOKEN not in c["argv"] for c in cli())


def test_env_and_input_reach_the_command_intact(cli):
    env = {"A": "sp ace 'q' \"d\" $HOME back\\slash", "B": "line1\nline2\n\n", "C": "tail\n"}
    data = b"one\n.\nthree\n\n"
    code = ("import json,os,sys;print(json.dumps({'env':{k:os.environ[k] for k in 'ABC'},"
            "'in':sys.stdin.buffer.read().decode()}))")
    done = osh._VM("sb").run_process("python3", ["-c", code], env=env, input=data)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"env": env, "in": data.decode()}


def test_a_nul_in_a_value_is_refused(cli):
    with pytest.raises(ValueError):
        osh._VM("sb").run_process("true", env={"A": "x\0y"})
    with pytest.raises(ValueError):
        osh._VM("sb").run_process("true", env={"A B": "x"})
    assert cli() == []


def test_a_started_agent_reads_an_empty_stdin(cli):
    code = "import os,sys;print(len(sys.stdin.buffer.read()), os.environ['K'])"
    proc = osh._VM("sb").create_process("python3", ["-c", code], env={"K": "v"})
    assert list(proc.stdout) == ["0 v\n"]
    assert proc.wait() == 0


def test_exit_codes_and_stderr_pass_through(cli):
    vm = osh._VM("sb")
    done = vm.run_process("sh", ["-c", "echo out; echo err >&2; exit 7"])
    assert (done.returncode, done.stdout, done.stderr) == (7, "out\n", "err\n")
    proc = vm.create_process("sh", ["-c", "echo out; echo err >&2; exit 7"])
    assert list(proc.stdout) == ["out\n"]
    assert proc.wait() == 7
    assert proc.stderr.read() == "err\n"
    echoed = vm.run_process("sh", ["-c", f"echo {osh.SENTINEL} >&2"])
    assert echoed.stderr == osh.SENTINEL + "\n"


def test_a_cli_failure_raises_the_adapters_error(cli, monkeypatch):
    monkeypatch.setenv("FAKE_OPENSHELL_MODE", "down")
    vm = osh._VM("sb")
    with pytest.raises(osh.OpenShellError):
        vm.run_process("true")
    with pytest.raises(osh.OpenShellError):
        vm.create_process("true").wait()
    assert osh.OpenShellError in osh.OpenShell.errors
    box = osh.OpenShell.__new__(osh.OpenShell)
    box.name, box.vm = "openshell", "sb"
    with pytest.raises(OSError):
        box._call(vm.run_process, "true")


def test_kill_after_gives_124(cli, monkeypatch):
    monkeypatch.setattr(osh, "KILL_SLACK", 0)
    done = osh._VM("sb").run_process("sleep", ["5"], kill_after=0.5)
    assert done.returncode == 124


def expected_rules():
    return {
        "anthropic_api": {"endpoints": [{"host": "api.anthropic.com", "port": 443, "protocol": "rest",
                                         "enforcement": "enforce", "access": "full"}],
                          "binaries": [{"path": osh.CLAUDE_EXE}]},
        "package_registries": {"endpoints": [
            {"host": "pypi.org", "port": 443, "protocol": "rest", "enforcement": "enforce", "access": "read-only"},
            {"host": "files.pythonhosted.org", "port": 443, "protocol": "rest", "enforcement": "enforce",
             "access": "read-only"},
            {"host": "registry.npmjs.org", "port": 443, "protocol": "rest", "enforcement": "enforce",
             "access": "read-only", "allow_encoded_slash": True}],
            "binaries": [{"path": osh.ANY_BINARY}]}}


def test_the_default_policy():
    folder = Path("/home/u/repo")
    assert osh.policy(folder, []) == {
        "version": 1,
        "filesystem_policy": {"include_workdir": True,
                              "read_only": ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log"],
                              "read_write": ["/tmp", "/dev/null", "/home/u/repo"]},
        "landlock": {"compatibility": "hard_requirement"},
        "network_policies": expected_rules()}
    assert "github" not in osh.policy_yaml(folder, [])


def test_sandbox_allow_entries_become_rules():
    allow = repo_settings.RepoSettings.model_validate({"sandbox_allow": [
        {"binary": "*", "host": "api.example.com", "access": "read-only"},
        {"binary": "/usr/bin/curl", "host": "x.example.org", "port": 8443, "access": "full"}]}).sandbox_allow
    rules = osh.policy(Path("/home/u/r"), allow)["network_policies"]
    assert {k: v for k, v in rules.items() if not k.startswith("repo_allow")} == expected_rules()
    assert rules["repo_allow_0"] == {"endpoints": [{"host": "api.example.com", "port": 443, "protocol": "rest",
                                                    "enforcement": "enforce", "access": "read-only"}],
                                     "binaries": [{"path": osh.ANY_BINARY}]}
    assert rules["repo_allow_1"]["endpoints"][0]["port"] == 8443
    assert rules["repo_allow_1"]["endpoints"][0]["access"] == "full"
    assert rules["repo_allow_1"]["binaries"] == [{"path": "/usr/bin/curl"}]
    assert "github" not in json.dumps(rules)
    entry = {"binary": "*", "host": "h", "access": "read-only"}
    for bad in ({**entry, "access": "write"}, {**entry, "binary": "bin/curl"}, {**entry, "extra": 1},
                {**entry, "host": "https://h"}):
        with pytest.raises(Exception):
            repo_settings.RepoSettings.model_validate({"sandbox_allow": [bad]})


def test_data_dir_reads_the_shipped_config(tmp_path):
    assert osh.data_dir(tmp_path) == "adws/adw_data"
    config = tmp_path / osh.CONFIG
    config.parent.mkdir(parents=True)
    shipped = SKILL / "templates" / "sssf.config.yaml"
    config.write_text(shipped.read_text())
    assert osh.data_dir(tmp_path) == "adws/adw_data"
    config.write_text(shipped.read_text().replace("data_dir: adws/adw_data", "data_dir: var/sssf"))
    assert osh.data_dir(tmp_path) == "var/sssf"


def layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "repo"
    repo.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    session = repo / "adws" / "adw_data" / "sessions" / "abc"
    handoff = session / "context_handoff"
    handoff.mkdir(parents=True)
    events = []
    run = types.SimpleNamespace(adw_id="abc", context_handoff_dir=handoff, session_dir=session,
                                tracer=types.SimpleNamespace(event=lambda e: events.append((e.name, e.payload))),
                                console=types.SimpleNamespace(note=lambda t: None))
    return repo, run, events


def test_create_writes_the_policy_and_passes_no_upload(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    box = osh.OpenShell(run, "sssf/abc", "my repo")
    vm = box._create()
    create = next(c["argv"] for c in cli() if c["argv"][:2] == ["sandbox", "create"])
    folder = osh.folder_for(repo)
    assert create[create.index("--from") + 1] == osh.image_tag(folder)
    assert create[create.index("--policy") + 1] == str(run.session_dir / "openshell-policy.yaml")
    assert f"sssf={osh.project_label('my repo')}" in create and "adw_id=abc" in create
    assert "--detach" in create and "--no-tty" in create
    assert not {"--upload", "--env", "--env-from"} & set(create)
    assert vm.name == create[create.index("--name") + 1] and vm.name.startswith("sssf-abc-")
    assert (run.session_dir / "openshell-policy.yaml").is_file()


def test_create_refuses_a_tree_outside_the_folder(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    run.session_dir = tmp_path / "elsewhere"
    run.session_dir.mkdir()
    monkeypatch.chdir(repo)
    with pytest.raises(osh.OpenShellError, match="openshell-setup"):
        osh.OpenShell(run, "sssf/abc", "p")._create()
    assert not any(c["argv"][:2] == ["sandbox", "create"] for c in cli())


def shipped():
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    for agent in cfg.agents:
        agent.coding_agent = "claude_code"
    return cfg


def test_check_roster_refuses_pi_under_openshell():
    cfg = shipped()
    cfg.agents[0].coding_agent = "pi"
    with pytest.raises(SystemExit) as stop:
        sandbox.check_roster("openshell", cfg)
    assert "pi" in str(stop.value) and "Claude Code" in str(stop.value)
    assert cfg.agents[0].name in str(stop.value)


def test_check_roster_passes_claude_code_under_openshell():
    sandbox.check_roster("openshell", shipped())
    assert "openshell" in sandbox.PROVIDERS


def test_sweep_deletes_only_dead_runs_of_this_project(cli, monkeypatch):
    monkeypatch.setenv("FAKE_OPENSHELL_NAMES", "sssf-live1-aaaaaa\nsssf-dead1-bbbbbb\nother-thing")
    assert osh.sweep("proj", {"live1"}) == ["sssf-dead1-bbbbbb (dead1)"]
    calls = [c["argv"] for c in cli()]
    assert calls[0][:4] == ["sandbox", "list", "--selector", f"sssf={osh.project_label('proj')}"]
    assert [c for c in calls if c[:2] == ["sandbox", "delete"]] == [["sandbox", "delete", "sssf-dead1-bbbbbb"]]


LABEL_VALUE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9_.-]*[A-Za-z0-9])?")

COLLIDING = [("my app", "my-app"), ("app+x", "app-x"), ("café", "caf-"),
             ("a" * 63 + "b", "a" * 63 + "c")]


@pytest.mark.parametrize("one,two", COLLIDING)
def test_names_the_old_label_merged_get_distinct_labels(one, two):
    assert osh.label(one) == osh.label(two)            # the baseline's collision
    assert osh.project_label(one) != osh.project_label(two)


@pytest.mark.parametrize("name", [n for pair in COLLIDING for n in pair] + [
    "x" * 200, "日本語のプロジェクト", "café" * 60, "", "-lead", "trail-", ".dot", "_u",
    "a,b=c", "my repo", "caf\udcc3\udca9", "\udcff" * 3])
def test_every_project_label_passes_openshell_limits(name):
    value = osh.project_label(name)
    assert len(value) <= osh.LABEL_MAX and LABEL_VALUE.fullmatch(value)
    assert value == osh.project_label(name)


def test_the_label_keeps_a_readable_prefix():
    assert osh.project_label("my-repo").startswith("my-repo-")
    assert osh.project_label("café") != osh.project_label("caf\udcc3\udca9")


def test_create_labels_what_this_projects_sweep_lists(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    osh.OpenShell(run, "sssf/abc", "my repo")._create()
    create = next(c["argv"] for c in cli() if c["argv"][:2] == ["sandbox", "create"])
    made = [create[i + 1] for i, a in enumerate(create) if a == "--label"
            and create[i + 1].startswith("sssf=")]
    osh.sweep("my repo", set())
    osh.sweep("my-repo", set())
    selectors = [c["argv"][3] for c in cli() if c["argv"][:3] == ["sandbox", "list", "--selector"]]
    assert made == [selectors[0]]
    assert selectors[1] != selectors[0]


def call_end_box():
    events = []
    box = osh.OpenShell.__new__(osh.OpenShell)
    box.vm = "sb"
    box._log = lambda name, payload: events.append((name, payload))
    return box, events


def test_denial_lines_become_events(cli, tmp_path, monkeypatch):
    logs = tmp_path / "logs.txt"
    logs.write_text("12:00 NET:OPEN [MED] DENIED /usr/bin/curl(0) -> github.com:443 [reason:transparent_tcp_policy_denied]\n"
                    "12:01 NET:OPEN [MED] DENIED /usr/bin/pip(12) -> evil.example:80 [reason:x]\n"
                    "12:02 NET:OPEN [INFO] ALLOWED /usr/bin/uv(3) -> pypi.org:443\n")
    monkeypatch.setenv("FAKE_OPENSHELL_LOGS", str(logs))
    box, events = call_end_box()
    import time
    box._call_end(time.time() - 5)
    assert [(n, p["binary"], p["destination"]) for n, p in events] == [
        ("sandbox_denied", "/usr/bin/curl", "github.com:443"),
        ("sandbox_denied", "/usr/bin/pip", "evil.example:80")]
    argv = next(c["argv"] for c in cli() if c["argv"][0] == "logs")
    assert argv[argv.index("--since") + 1].endswith("s")


def test_a_failed_log_read_is_a_sandbox_error(cli):
    box, events = call_end_box()
    import time
    box._call_end(time.time())
    assert [n for n, _ in events] == ["sandbox_error"]


def test_require_sdk_names_the_recipe(cli, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(SystemExit) as stop:
        osh.require_sdk()
    assert "openshell-setup" in str(stop.value)


def test_require_sdk_names_the_recipe_when_the_gateway_is_down(cli, monkeypatch):
    monkeypatch.setenv("FAKE_OPENSHELL_CODE", "1")
    with pytest.raises(SystemExit) as stop:
        osh.require_sdk()
    assert "openshell-setup" in str(stop.value) and "gateway" in str(stop.value)


def test_other_providers_call_end_is_a_no_op():
    assert Remote._call_end(Remote.__new__(Remote), 0.0) is None
    box, events = call_end_box()
    box.__class__ = type("Plain", (Remote,), {})
    box._call_end(0.0)
    assert events == []
