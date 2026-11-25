"""adw_modules/sandbox_openshell.py: `--sandbox openshell` drives the `openshell`
CLI. A fake one, first on PATH, stands in; no gateway, sandbox or Podman runs.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import base64
import importlib.util
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, repo_settings, sandbox, sandbox_openshell as osh, utils  # noqa: E402
from adw_modules import sandbox_remote  # noqa: E402
from adw_modules.sandbox_remote import Remote  # noqa: E402

FAKE = '''#!{python}
import base64, hashlib, json, os, shutil, subprocess, sys, time
argv = sys.argv[1:]
data = sys.stdin.buffer.read() if argv[:2] == ["sandbox", "exec"] else b""
# FAKE_OPENSHELL_MAP: "from=to" pairs, one per line. The fake's VM is the host, so
# these give the VM its own root for the paths a test names. Unset: no change.
pairs = [p.split("=", 1) for p in os.environ.get("FAKE_OPENSHELL_MAP", "").splitlines() if p]
def vm(text):
    for old, new in pairs:
        if old in text:
            return text.replace(old, new)
    return text
entry = {{"argv": argv, "stdin": base64.b64encode(data).decode() if len(data) <= 65536 else "",
          "stdin_len": len(data)}}
if argv[:2] == ["sandbox", "upload"]:
    rest = [a for a in argv[2:] if not a.startswith("--")]
    local, dest = rest[1], rest[2]
    folder = os.path.dirname(local)
    entry["upload"] = {{"local": local, "dest": dest, "symlink": os.path.islink(local),
                       "listing": sorted(os.listdir(folder)), "size": os.path.getsize(local),
                       "sha256": hashlib.sha256(open(local, "rb").read()).hexdigest()}}
with open(os.environ["FAKE_OPENSHELL_LOG"], "a") as f:
    f.write(json.dumps(entry) + "\\n")
code = int(os.environ.get("FAKE_OPENSHELL_CODE", "0"))
if argv[:2] == ["sandbox", "upload"]:
    if "--no-git-ignore" not in argv and subprocess.run(
            ["git", "-C", os.path.dirname(local), "rev-parse", "--is-inside-work-tree"],
            capture_output=True).returncode == 0:
        sys.stderr.write("error: .gitignore filtering selected no files\\n")
        sys.exit(1)
    target = vm(dest)
    if os.path.isdir(target) or target.endswith("/"):
        target = os.path.join(target, os.path.basename(local))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(local, target)
    sys.exit(code)
if argv[:2] == ["sandbox", "exec"]:
    if os.environ.get("FAKE_OPENSHELL_MODE") == "down":
        sys.stderr.write("error: gateway unreachable\\n")
        sys.exit(1)
    if len(data) > 4 * 1024 * 1024:
        sys.stderr.write("error: piped stdin exceeds the 4 MiB limit; use `sandbox upload` for larger input\\n")
        sys.exit(1)
    cmd = [vm(w) for w in argv[argv.index("--") + 1:]]
    cwd = vm(argv[argv.index("--workdir") + 1]) if "--workdir" in argv else None
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
                              "read_only": ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/dev/zero",
                                            "/dev/random", "/etc", "/var/log"],
                              "read_write": ["/tmp", "/var/tmp", "/dev/null", "/home/u/repo"]},
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
                                data_root=repo / "adws" / "adw_data",
                                tracer=types.SimpleNamespace(event=lambda e: events.append((e.name, e.payload))),
                                console=types.SimpleNamespace(note=lambda t: None))
    return repo, run, events


def write_shipped_config(repo: Path) -> None:
    config = repo / osh.CONFIG
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text((SKILL / "templates" / "sssf.config.yaml").read_text())


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
    run.data_root = tmp_path / "elsewhere"
    run.session_dir = run.data_root / "sessions" / "abc"
    run.session_dir.mkdir(parents=True)
    monkeypatch.chdir(repo)
    with pytest.raises(osh.OpenShellError, match="openshell-setup"):
        osh.OpenShell(run, "sssf/abc", "p")._create()
    assert not any(c["argv"][:2] == ["sandbox", "create"] for c in cli())


def shipped():
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    for agent in cfg.agents:
        agent.coding_agent = "claude_code"
    return cfg


def roster(*kinds):
    """The shipped config, its agents cycled through `kinds`; each pi agent on an openai/ model."""
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    for agent, kind in zip(cfg.agents, itertools.cycle(kinds)):
        agent.coding_agent = kind
        if kind == "pi":
            agent.model = "openai/gpt-5.6-terra"
    return cfg


def test_check_roster_lets_a_pi_openai_roster_through_under_openshell():
    sandbox.check_roster("openshell", roster("pi"))


@pytest.mark.parametrize("provider", sandbox.PROVIDERS)
def test_a_pi_agent_on_another_provider_is_refused_in_every_vm_mode(provider):
    cfg = roster("pi")
    sandbox.check_roster(provider, cfg)
    cfg.agents[0].model = "google/gemini-3.6-flash"
    with pytest.raises(SystemExit) as stop:
        sandbox.check_roster(provider, cfg)
    assert cfg.agents[0].name in str(stop.value)


KINDS = {"pi": ("pi",), "claude": ("claude_code",), "mixed": ("pi", "claude_code")}
OPENAI = {"endpoints": [{"host": "api.openai.com", "port": 443, "protocol": "rest",
                         "enforcement": "enforce", "access": "full"}],
          "binaries": [{"path": "/usr/local/bin/node"}]}


@pytest.mark.parametrize("kind, models", [("pi", {"openai_api"}), ("claude", {"anthropic_api"}),
                                          ("mixed", {"openai_api", "anthropic_api"})])
def test_policy_has_a_model_rule_only_for_the_harnesses_it_has(kind, models):
    rules = osh.policy(F, [], sandbox_remote.harnesses(roster(*KINDS[kind])))["network_policies"]
    assert set(rules) == models | {"package_registries"}
    if "openai_api" in models:
        assert rules["openai_api"] == OPENAI
    assert "github" not in rules


def test_an_empty_roster_gets_no_model_rule():
    assert set(osh.policy(F, [], set())["network_policies"]) == {"package_registries"}


@pytest.mark.parametrize("kind, present, absent", [("pi", "api.openai.com", "api.anthropic.com"),
                                                   ("claude", "api.anthropic.com", "api.openai.com")])
def test_create_writes_only_its_rosters_model_host(cli, tmp_path, monkeypatch, kind, present, absent):
    repo, run, _ = layout(tmp_path, monkeypatch)
    run.cfg = roster(*KINDS[kind])
    monkeypatch.chdir(repo)
    osh.OpenShell(run, "sssf/abc", "p")._create()
    text = (run.session_dir / "openshell-policy.yaml").read_text()
    rules = yaml.safe_load(text)["network_policies"]
    hosts = {e["host"] for r in rules.values() for e in r["endpoints"]}
    assert present in hosts and absent not in hosts
    create = next(c["argv"] for c in cli() if c["argv"][:2] == ["sandbox", "create"])
    assert "--no-auto-providers" in create


def test_containerfile_holds_pi_ripgrep_and_fd():
    text = osh.containerfile(F, "")
    assert "RUN npm install -g @earendil-works/pi-coding-agent@1.5.0" in text
    apt = next(l for l in text.splitlines() if "apt-get install" in l)
    assert "ripgrep" in apt and "fd-find" in apt
    check = next(l for l in text.splitlines() if l.startswith("RUN node --version"))
    assert "pi --version" in check and "rg --version" in check and "fdfind --version" in check


def test_the_image_holds_rsync_and_nothing_else_changed():
    import hashlib
    text = osh.containerfile(F, "")
    apts = [l for l in text.splitlines() if "apt-get install" in l]
    assert len(apts) == 1
    assert apts[0].endswith(
        " git curl ca-certificates python3 xz-utils ripgrep fd-find rsync && rm -rf /var/lib/apt/lists/*")
    before = text.replace(" fd-find rsync &&", " fd-find &&")
    assert before != text
    assert ("localhost/sssf-openshell:" + hashlib.sha256(before.encode()).hexdigest()[:12]
            == "localhost/sssf-openshell:3028d837a77a")
    assert "rsync" not in osh.policy_yaml(F, [])
    assert "rsync" not in osh.policy_yaml(F, [], frozenset({"claude_code", "pi"}))
    assert osh.READ_ONLY_PATHS == ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/dev/zero", "/dev/random",
                                   "/etc", "/var/log"]
    assert osh.REGISTRIES == ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org")


def test_the_e2b_script_reads_the_shared_pi_version():
    path = SKILL / "scripts" / "e2b_template.py"
    spec = importlib.util.spec_from_file_location("e2b_template_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PI == sandbox_remote.PI_VERSION == "1.5.0"


def _started_env(cli, tmp_path, monkeypatch, env):
    repo, run, _ = layout(tmp_path, monkeypatch)
    box = osh.OpenShell(run, "sssf/abc", "p")
    box.sandbox = osh._VM("sb")
    box._token = TOKEN
    monkeypatch.setattr(Remote, "_refresh", lambda self, seconds: None)
    monkeypatch.setattr(Remote, "handoff_in", lambda self: None)
    box.start(["true"], cwd=repo, env=env)
    for _ in range(100):
        calls = cli()
        if any(c["stdin"] and "setsid" in c["argv"] for c in calls):
            break
        time.sleep(0.1)
    frame = next(stdin_of(c) for c in calls if c["stdin"] and "setsid" in c["argv"])
    values = {}
    for line in frame.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = base64.b64decode(value).decode()
    return values, calls


def test_a_pi_start_gets_the_chatgpt_token_and_pi_offline_not_the_claude_token(cli, tmp_path, monkeypatch):
    values, calls = _started_env(cli, tmp_path, monkeypatch, {"SSSF_PI_TOKEN": "chatgpt-access"})
    assert values["OPENAI_API_KEY"] == "chatgpt-access" and values["PI_OFFLINE"] == "1"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in values
    assert values["JITI_FS_CACHE"] == "false"
    assert values["NODE_DISABLE_COMPILE_CACHE"] == "1"
    assert not any("chatgpt-access" in " ".join(c["argv"]) or TOKEN in " ".join(c["argv"]) for c in calls)


def test_a_claude_start_keeps_the_claude_token(cli, tmp_path, monkeypatch):
    values, calls = _started_env(cli, tmp_path, monkeypatch, {"SSSF_X": "1"})
    assert values["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert "PI_OFFLINE" not in values and "OPENAI_API_KEY" not in values
    assert "JITI_FS_CACHE" not in values
    assert "NODE_DISABLE_COMPILE_CACHE" not in values
    assert not any(TOKEN in " ".join(c["argv"]) for c in calls)


def test_an_all_pi_roster_opens_without_a_token_file(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    run.cfg = roster("pi")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sandbox_remote, "TOKEN_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(osh.OpenShell, "_create", lambda self: osh._VM("sb"))
    for name in ("_clone", "handoff_in", "_check_tools"):
        monkeypatch.setattr(osh.OpenShell, name, lambda self, *a, **k: None)
    box = osh.OpenShell(run, "sssf/abc", "p")
    box.open()
    assert box._token == ""


def test_check_roster_passes_claude_code_under_openshell():
    sandbox.check_roster("openshell", shipped())
    assert "openshell" in sandbox.PROVIDERS


def test_sweep_deletes_only_dead_runs_of_this_project(cli, monkeypatch):
    monkeypatch.setenv("FAKE_OPENSHELL_NAMES", "sssf-live1-aaaa\nsssf-dead1-bbbb\nsssf-dead2-cccccc\nother-thing")
    assert osh.sweep("proj", {"live1"}) == ["sssf-dead1-bbbb (dead1)"]
    calls = [c["argv"] for c in cli()]
    assert calls[0][:4] == ["sandbox", "list", "--selector", f"sssf={osh.project_label('proj')}"]
    assert [c for c in calls if c[:2] == ["sandbox", "delete"]] == [["sandbox", "delete", "sssf-dead1-bbbb"]]


def test_every_name_create_makes_fits_the_cli_and_sweeps_back(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    write_shipped_config(repo)
    monkeypatch.chdir(repo)
    run.adw_id = utils.new_id()
    names = []
    for _ in range(3):
        vm = osh.OpenShell(run, "sssf/x", "my repo")._create()
        names.append(vm.name)
    creates = [c["argv"] for c in cli() if c["argv"][:2] == ["sandbox", "create"]]
    assert len(creates) == 3
    for vm_name, create in zip(names, creates):
        assert len(vm_name) <= osh.OpenShell.sandbox_name_max
        assert vm_name == create[create.index("--name") + 1]
        assert vm_name.startswith(f"sssf-{run.adw_id}-")
    monkeypatch.setenv("FAKE_OPENSHELL_NAMES", "\n".join(names))
    assert osh.sweep("my repo", set()) == [f"{n} ({run.adw_id})" for n in names]


def test_create_refuses_a_name_over_the_cli_limit(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    write_shipped_config(repo)
    monkeypatch.chdir(repo)
    run.adw_id = "a" * 10
    with pytest.raises(sandbox.SandboxFailed, match="19"):
        osh.OpenShell(run, "sssf/x", "my repo")._create()
    assert not any(c["argv"][:2] == ["sandbox", "create"] for c in cli())
    assert osh.OpenShell.sandbox_name_max == 19


def test_setup_recipe_points_to_the_skills_config_reference():
    lines = (SKILL / "templates" / "justfile").read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("openshell-setup:"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i] and not lines[i][0].isspace()), len(lines))
    recipe = "\n".join(lines[start:end])
    assert "references/config.md" in recipe and "docs/research" not in recipe
    config = (SKILL / "references" / "config.md").read_text()
    assert "**Installing OpenShell.**" in config and "SHA-256" in config


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


# --- the repo's Python in the image ------------------------------------------

F = Path("/home/u/repo")


def test_containerfile_installs_and_checks_the_python():
    text = osh.containerfile(F, "3.13")
    installs = [l for l in text.splitlines() if l.startswith("RUN ") and "uv python install 3.13" in l]
    assert len(installs) == 1
    assert "UV_PYTHON_INSTALL_DIR=/usr/" in installs[0] and "UV_PYTHON_BIN_DIR=/usr/local/bin" in installs[0]
    assert osh.PYTHON_DIR.startswith("/usr/") and osh.PYTHON_BIN.startswith("/usr/")
    assert "/usr" in osh.READ_ONLY_PATHS
    runs = [l for l in text.splitlines() if l.startswith("RUN ")]
    assert runs[-1].endswith("&& uv python find 3.13")


def test_containerfile_without_a_python_installs_none():
    text = osh.containerfile(F, "")
    assert "uv python" not in text
    # the tag once the image holds rsync
    assert osh.image_tag(F, "") == "localhost/sssf-openshell:87ec13f36d5e"


def test_two_pythons_give_two_image_tags():
    tags = {osh.image_tag(F, "3.12"), osh.image_tag(F, "3.13"), osh.image_tag(F, "")}
    assert len(tags) == 3


@pytest.mark.parametrize("bad", ["3.13; curl x|sh", "3", "3.13.1", "3.13\n"])
def test_containerfile_refuses_a_malformed_python(bad):
    with pytest.raises(ValueError):
        osh.containerfile(F, bad)
    with pytest.raises(ValueError):
        osh.image_tag(F, bad)


def test_require_sdk_refuses_when_the_image_for_the_hosts_python_is_missing(cli, tmp_path, monkeypatch):
    podman = tmp_path / "bin" / "podman"
    podman.write_text(f'#!{sys.executable}\n'
                      'import os, sys\n'
                      'ok = sys.argv[1:3] == ["image", "exists"] and sys.argv[3:] == [os.environ["FAKE_PODMAN_HAVE"]]\n'
                      'sys.exit(0 if ok else 1)\n')
    podman.chmod(0o755)
    repo, _, _ = layout(tmp_path, monkeypatch)
    write_shipped_config(repo)
    monkeypatch.chdir(repo)
    folder = osh.folder_for(repo)
    monkeypatch.setenv("FAKE_PODMAN_HAVE", osh.image_tag(folder, "3.12"))
    monkeypatch.setattr(osh, "image_python", lambda p: "3.13")
    with pytest.raises(SystemExit) as refused:
        osh.require_sdk()
    text = str(refused.value)
    assert "openshell-setup" in text and osh.image_tag(folder, "3.13") in text and "isn't built" in text
    monkeypatch.setattr(osh, "image_python", lambda p: "3.12")
    assert osh.require_sdk() is None


def test_create_builds_from_the_image_for_the_hosts_python(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    write_shipped_config(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(osh, "image_python", lambda p: "3.13")
    osh.OpenShell(run, "sssf/abc", "my repo")._create()
    create = next(c["argv"] for c in cli() if c["argv"][:2] == ["sandbox", "create"])
    assert create[create.index("--from") + 1] == osh.image_tag(osh.folder_for(repo), "3.13")


def test_the_image_pins_bun_and_checks_it():
    text = osh.containerfile(F, "")
    assert osh.BUN == "1.4.4"
    assert "RUN npm install -g bun@1.4.4" in text.splitlines()
    assert "ignore-scripts" not in text
    assert "&& bun --version" in next(l for l in text.splitlines() if l.startswith("RUN node --version"))
    tag = osh.image_tag(F, "")
    osh.BUN = "1.4.5"
    try:
        assert osh.image_tag(F, "") != tag
    finally:
        osh.BUN = "1.4.4"


def test_the_image_check_fails_when_bun_is_missing(tmp_path):
    line = next(l for l in osh.containerfile(F, "").splitlines() if l.startswith("RUN node --version"))[4:]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("node", "uv", "claude", "pi", "git", "fdfind", "rg"):
        (bin_dir / name).write_text("#!/bin/sh\necho " + name + " 1.0\n")
        (bin_dir / name).chmod(0o755)
    (bin_dir / "head").symlink_to(shutil.which("head"))
    done = subprocess.run([shutil.which("sh"), "-c", line], capture_output=True, text=True,
                          env={"PATH": str(bin_dir)})
    assert done.returncode != 0


@pytest.mark.parametrize("rg", ["present", "missing", "fails"])
def test_the_image_check_fails_when_rg_is_missing(tmp_path, rg):
    line = next(l for l in osh.containerfile(F, "").splitlines() if l.startswith("RUN node --version"))[4:]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ran_fd = tmp_path / "fd-ran"

    def stub(name, body):
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n")
        (bin_dir / name).chmod(0o755)
    for name in ("node", "uv", "claude", "pi", "bun", "git"):
        stub(name, "echo " + name + " 1.0")
    stub("fdfind", f"echo ran > {ran_fd}")
    (bin_dir / "head").symlink_to(shutil.which("head"))
    if rg != "missing":
        stub("rg", "echo rg first line\necho rg second line" + ("\nexit 1" if rg == "fails" else ""))
    done = subprocess.run([shutil.which("sh"), "-c", line], capture_output=True, text=True,
                          env={"PATH": str(bin_dir)})
    if rg == "present":
        assert done.returncode == 0
        assert "rg first line" in done.stdout and "rg second line" not in done.stdout
    else:
        assert done.returncode != 0 and not ran_fd.exists()


# --- big inputs go by upload; the key check judges what reaches the VM ---------

import getpass  # noqa: E402
import hashlib  # noqa: E402


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def big_repo(tmp_path_factory):
    """A git repo holding a 22 MB random file, committed on the branch sssf/abc."""
    repo = tmp_path_factory.mktemp("bigrepo")
    _git("init", "-q", "-b", "sssf/abc", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    (repo / "big.bin").write_bytes(os.urandom(22 * 1024 * 1024))
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "big", cwd=repo)
    return repo


def _vm_box(cli, tmp_path, monkeypatch, big_repo):
    """An OpenShell box on a copy of the big repo, whose VM root is tmp_path/vm."""
    repo, run, events = layout(tmp_path, monkeypatch)
    shutil.rmtree(repo)
    shutil.copytree(big_repo, repo, symlinks=True)
    run.context_handoff_dir.mkdir(parents=True)
    vmroot = tmp_path / "vm"
    (vmroot / "tmp").mkdir(parents=True)
    monkeypatch.setenv("FAKE_OPENSHELL_MAP", f"{repo}={vmroot}/repo\n/tmp/sssf-in={vmroot}/tmp/sssf-in")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(osh.OpenShell, "owner", getpass.getuser())
    monkeypatch.setattr(sandbox_remote, "_host_python", lambda tree, problems: "")
    monkeypatch.setattr(osh.OpenShell, "_create", lambda self: osh._VM("sb"))
    monkeypatch.setattr(osh.OpenShell, "_check_tools", lambda self: None)
    run.cfg = roster("pi")
    monkeypatch.chdir(repo)
    box = osh.OpenShell(run, "sssf/abc", "p")
    box.tree = repo / "wt"      # empty in the VM, as a run's worktree is before the clone
    box.tree.mkdir()
    return box, repo, run, vmroot


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _uploads(cli):
    return [c for c in cli() if "upload" in c]


def test_big_bundle_clones_by_upload(cli, tmp_path, monkeypatch, big_repo):
    box, repo, run, vmroot = _vm_box(cli, tmp_path, monkeypatch, big_repo)
    box.open()
    ups = _uploads(cli)
    assert len(ups) == 1
    up = ups[0]["upload"]
    assert up["size"] > osh.STDIN_LIMIT
    assert _sha(vmroot / "tmp" / "sssf-in.bundle") == up["sha256"]
    assert not list((vmroot / "tmp").glob("sssf-in-*"))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    vm_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=vmroot / "repo" / "wt", capture_output=True, text=True).stdout
    assert head == vm_head != ""


def test_stdin_limit_fails_like_openshell(cli, tmp_path, monkeypatch, big_repo):
    box, repo, run, vmroot = _vm_box(cli, tmp_path, monkeypatch, big_repo)
    monkeypatch.setattr(osh, "STDIN_MAX", 1 << 30)
    # `_call` turns the adapter's error into an OSError, which `open` doesn't wrap.
    with pytest.raises((sandbox.SandboxFailed, OSError), match="piped stdin exceeds the 4 MiB limit"):
        box.open()
    assert _uploads(cli) == []


def test_big_handoff_goes_in_and_comes_back(cli, tmp_path, monkeypatch, big_repo):
    box, repo, run, vmroot = _vm_box(cli, tmp_path, monkeypatch, big_repo)
    box.sandbox, box.vm = osh._VM("sb"), "sb"
    host_file = run.context_handoff_dir / "in.bin"
    host_file.write_bytes(os.urandom(5 * 1024 * 1024))
    vm_handoff = vmroot / "repo" / run.context_handoff_dir.relative_to(repo)
    vm_handoff.mkdir(parents=True)
    box.handoff_in()
    assert (vm_handoff / "in.bin").read_bytes() == host_file.read_bytes()
    assert len(_uploads(cli)) == 1
    made = os.urandom(5 * 1024 * 1024)
    (vm_handoff / "out.bin").write_bytes(made)
    box.handoff_out()
    assert (run.context_handoff_dir / "out.bin").read_bytes() == made


@pytest.mark.parametrize("plant", ["file", "symlink"])
def test_staged_upload_holds_only_the_input(cli, tmp_path, monkeypatch, big_repo, plant):
    box, repo, run, vmroot = _vm_box(cli, tmp_path, monkeypatch, big_repo)
    planted = run.session_dir / "sandbox-in.bundle"
    decoy = tmp_path / "decoy.bin"
    decoy.write_bytes(b"DECOY-BYTES" * 1000)
    if plant == "file":
        planted.write_bytes(b"PLANTED-BYTES" * 1000)
    else:
        planted.symlink_to(decoy)
    box.open()
    ups = _uploads(cli)
    assert len(ups) == 1
    up, argv = ups[0]["upload"], ups[0]["argv"]
    local = Path(up["local"])
    for folder in (repo, run.session_dir, run.data_root):
        assert folder not in local.parents
    assert not up["symlink"] and up["listing"] == ["input"]
    assert "--no-git-ignore" in argv
    assert not local.parent.exists()
    vm_bundle = (vmroot / "tmp" / "sssf-in.bundle").read_bytes()
    assert hashlib.sha256(vm_bundle).hexdigest() == up["sha256"]
    assert b"PLANTED-BYTES" not in vm_bundle and b"DECOY-BYTES" not in vm_bundle


def test_small_input_stays_on_the_pipe(cli):
    done = osh._VM("sb").run_process("cat", input=b"abc")
    assert done.stdout == "abc"
    assert _uploads(cli) == []
    argv = cli()[0]["argv"]
    assert argv[-2:] == ["input", "cat"]


def test_nonessential_traffic_is_off_for_claude_only(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "0")
    values, _ = _started_env(cli, tmp_path, monkeypatch,
                             {"SSSF_X": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "0"})
    assert values["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    Path(os.environ["FAKE_OPENSHELL_LOG"]).unlink()     # the next frame is the only one
    values, _ = _started_env(cli, tmp_path / "pi", monkeypatch, {"SSSF_PI_TOKEN": "chatgpt-access"})
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in values
    assert Remote.cc_env == {}


def test_key_reaches_only_when_the_vm_would_get_it(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    run.cfg = roster("pi")
    made = []
    monkeypatch.setattr(sandbox_remote, "TOKEN_FILE", tmp_path / "token.env")
    (tmp_path / "token.env").write_text("CLAUDE_CODE_OAUTH_TOKEN=t\n")
    monkeypatch.setattr(osh.OpenShell, "_create", lambda self: made.append(1) or osh._VM("sb"))
    for name in ("_clone", "handoff_in", "_check_tools"):
        monkeypatch.setattr(osh.OpenShell, name, lambda self, *a, **k: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    osh.OpenShell(run, "sssf/abc", "p").open()
    assert made == [1]
    monkeypatch.setattr(sandbox_remote, "PASSED", (*sandbox_remote.PASSED, "ANTHROPIC_"))
    with pytest.raises(sandbox.SandboxFailed, match="ANTHROPIC_API_KEY"):
        osh.OpenShell(run, "sssf/abc", "p").open()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "k")
    with pytest.raises(sandbox.SandboxFailed, match="ANTHROPIC_AUTH_TOKEN"):
        osh.OpenShell(run, "sssf/abc", "p").open()
    assert made == [1]
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN")
    monkeypatch.setattr(osh.OpenShell, "cc_env", {"ANTHROPIC_API_KEY": "k"})
    monkeypatch.setattr(osh.OpenShell, "_create", lambda self: made.append(2) or osh._VM("sb"))
    with pytest.raises(sandbox.SandboxFailed, match="ANTHROPIC_API_KEY"):
        osh.OpenShell(run, "sssf/abc", "p").open()
    assert made == [1]


# --- TMPDIR: every exec writes temp files on the VM's disk, not the RAM tmpfs ----

def _tmpdir_lines(call) -> list[str]:
    return [l for l in stdin_of(call).splitlines() if l.startswith("TMPDIR=")]


def test_every_exec_carries_tmpdir_on_the_disk(cli):
    vm = osh._VM("sb")
    want = "TMPDIR=" + base64.b64encode(b"/var/tmp").decode()
    show = "import os;print(os.environ['TMP' + 'DIR'])"
    done = vm.run_process("python3", ["-c", show])
    assert done.stdout.strip() == "/var/tmp"
    vm.run_process("true", env={"TMPDIR": "/elsewhere", "A": "1"})
    vm.run_process("cat", input="hello")
    vm.create_process("true").wait()
    execs = [c for c in cli() if c["argv"][:2] == ["sandbox", "exec"]]
    assert len(execs) == 4
    for call in execs:
        assert _tmpdir_lines(call) == [want]
        assert "/elsewhere" not in stdin_of(call) and "TMPDIR" not in " ".join(call["argv"])


def test_a_runs_commands_and_agents_get_tmpdir(cli, tmp_path, monkeypatch):
    values, _ = _started_env(cli, tmp_path, monkeypatch, {"SSSF_X": "1", "TMPDIR": "/elsewhere"})
    assert values["TMPDIR"] == "/var/tmp"
    repo, run, _ = layout(tmp_path / "run", monkeypatch)
    box = osh.OpenShell(run, "sssf/abc", "p")
    box.sandbox = osh._VM("sb")
    monkeypatch.setattr(Remote, "_refresh", lambda self, seconds: None)
    done = box.run(["sh", "-c", "printf %s \"$TMPDIR\""], cwd=repo, env={"TMPDIR": "/elsewhere"})
    assert done.returncode == 0, done.stderr
    assert done.stdout == "/var/tmp"


def test_tmpdir_is_in_the_policy_and_outside_the_repo(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    write_shipped_config(repo)
    monkeypatch.chdir(repo)
    osh.OpenShell(run, "sssf/abc", "p")._create()
    written = yaml.safe_load((run.session_dir / "openshell-policy.yaml").read_text())
    writable = written["filesystem_policy"]["read_write"]
    assert osh.TMPDIR in writable and "/tmp" in writable
    folder = osh.folder_for(repo)
    assert Path(osh.TMPDIR) != folder and folder not in Path(osh.TMPDIR).parents


@pytest.mark.parametrize("kind", KINDS)
def test_a_vm_can_read_dev_zero_and_dev_random(kind):
    p = osh.policy(F, [], sandbox_remote.harnesses(roster(*KINDS[kind])))
    fs = p["filesystem_policy"]
    for device in ("/dev/zero", "/dev/random"):
        assert device in fs["read_only"]
        assert device not in fs["read_write"]


@pytest.mark.parametrize("kind", KINDS)
def test_the_policy_differs_from_the_baseline_only_by_the_two_devices(kind):
    harnesses = sandbox_remote.harnesses(roster(*KINDS[kind]))
    p = osh.policy(F, [], harnesses)
    fs = p["filesystem_policy"]
    rest = [x for x in fs["read_only"] if x not in ("/dev/zero", "/dev/random")]
    assert rest == ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log"]
    assert len(set(fs["read_only"])) == len(fs["read_only"])
    assert fs["read_write"] == ["/tmp", "/var/tmp", "/dev/null", str(F)]
    assert fs["include_workdir"] is True
    assert p["landlock"] == {"compatibility": "hard_requirement"}
    assert set(p) == {"version", "filesystem_policy", "landlock", "network_policies"}
    assert set(fs) == {"include_workdir", "read_only", "read_write"}
    assert p["version"] == 1
    expected = {"package_registries": expected_rules()["package_registries"]}
    if "claude_code" in KINDS[kind]:
        expected["anthropic_api"] = expected_rules()["anthropic_api"]
    if "pi" in KINDS[kind]:
        expected["openai_api"] = OPENAI
    assert p["network_policies"] == expected
    for closed in ("/dev/tty", "/dev/ptmx", "/dev/pts", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/full"):
        assert closed not in fs["read_only"] and closed not in fs["read_write"]
    text = osh.containerfile(F, "")
    assert "/dev/zero" not in text and "/dev/random" not in text


def test_create_writes_the_devices_into_the_policy_file(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    write_shipped_config(repo)
    monkeypatch.chdir(repo)
    osh.OpenShell(run, "sssf/abc", "p")._create()
    fs = yaml.safe_load((run.session_dir / "openshell-policy.yaml").read_text())["filesystem_policy"]
    for device in ("/dev/zero", "/dev/random"):
        assert device in fs["read_only"] and device not in fs["read_write"]


# --- the tool check looks up the repo's own commands ---------------------------

def _open_checked(cli, tmp_path, monkeypatch, big_repo, quality=None, setup=None, vm_tools=True):
    """A real OpenShell box, with the real tool check, whose roster tools are stand-ins."""
    box, repo, run, vmroot = _vm_box(cli, tmp_path, monkeypatch, big_repo)
    monkeypatch.setattr(osh.OpenShell, "_check_tools", Remote._check_tools)
    monkeypatch.setattr(sandbox_remote, "TOKEN_FILE", tmp_path / "token.env")
    bin_ = tmp_path / "bin"
    for name, text in (("node", "v24.11.1"), ("pi", "1.5.0"), ("uv", "uv 0.9.0")):
        (bin_ / name).write_text(f"#!/bin/sh\necho {text}\n")
        (bin_ / name).chmod(0o755)
    if quality is not None:
        probe = tmp_path / "quality.py"
        probe.write_text(quality)
        monkeypatch.setattr(sandbox_remote, "QUALITY_FILE", probe)
    if setup is not None:
        config = repo / "adws" / "adw_sssf_config" / "repo.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(yaml.safe_dump({"worktree_setup": setup}))
    return box


def _lookups(cli):
    return [c["argv"] for c in cli() if sandbox_remote.LOOKUP in c["argv"]]


QUALITY = ("def run_quality(run):\n    return [{block}(run)]\n\n"
           "def {block}(run):\n    return _run(QualityCheckSpec(name='x', argv={argv}), run)\n")


def test_a_check_command_missing_from_the_vm_stops_the_run_before_any_agent(cli, tmp_path, monkeypatch, big_repo):
    box = _open_checked(cli, tmp_path, monkeypatch, big_repo,
                        quality=QUALITY.format(block="test", argv='["sssf-no-such-tool", "test"]'))
    with pytest.raises(sandbox.SandboxFailed) as stopped:
        box.open()
    said = str(stopped.value)
    assert "sssf-no-such-tool is missing; quality.py's `test` check runs it" in said
    assert "can't run this repo's checks or setup" in said
    assert box.tools["sssf-no-such-tool"] == "missing"
    assert box.processes == 0
    assert not any("setsid" in " ".join(c["argv"]) for c in cli())


def test_a_setup_command_missing_from_the_vm_stops_the_run(cli, tmp_path, monkeypatch, big_repo):
    box = _open_checked(cli, tmp_path, monkeypatch, big_repo,
                        quality=QUALITY.format(block="test", argv="['git', 'status']"),
                        setup=["cd . && sssf-no-such-tool install"])
    with pytest.raises(sandbox.SandboxFailed, match="worktree_setup runs it"):
        box.open()


def test_commands_that_are_all_present_open_the_box(cli, tmp_path, monkeypatch, big_repo):
    box = _open_checked(cli, tmp_path, monkeypatch, big_repo,
                        quality=QUALITY.format(block="test", argv="['git', 'status']"),
                        setup=["cd . && ls"])
    box.open()
    assert box.tools["git"] != "missing" and box.tools["ls"] == "found"
    assert len(_lookups(cli)) == 1


def test_the_shipped_quality_file_sends_no_lookup(cli, tmp_path, monkeypatch, big_repo):
    box = _open_checked(cli, tmp_path, monkeypatch, big_repo)
    box.open()
    assert _lookups(cli) == []
    assert set(box.tools) == {"git", "uv", "node", "pi"}


@pytest.mark.parametrize("name", ["x; touch {m}", "$(touch {m})", "a\ntouch {m}"])
def test_a_hostile_command_name_is_never_sent_to_the_vm(cli, tmp_path, monkeypatch, big_repo, name):
    marker = tmp_path / "marker"
    hostile = name.format(m=marker)
    box = _open_checked(cli, tmp_path, monkeypatch, big_repo,
                        quality=QUALITY.format(block="test", argv=repr([hostile, "x"])),
                        # `x; touch <path>` is plain text: two commands, so `x` is looked up
                        setup=["git status"] if ";" in name else [hostile, "git status"])
    box.open()
    assert not marker.exists()
    for argv in _lookups(cli):
        tail = argv[argv.index("-c"):]
        assert tail[:3] == ["-c", sandbox_remote.LOOKUP, "sh"]
        assert all(sandbox_remote.NAME.fullmatch(n) for n in tail[3:])
    assert not any(hostile in " ".join(c["argv"]) for c in cli())


def test_checking_the_commands_runs_no_repo_code(cli, tmp_path, monkeypatch, big_repo):
    marker = tmp_path / "ran"
    box = _open_checked(cli, tmp_path, monkeypatch, big_repo,
                        quality=f"open({str(marker)!r}, 'w').write('x')\n"
                                + QUALITY.format(block="test", argv="['git', 'status']"))
    before = "adw_modules.quality" in sys.modules
    box.open()
    assert not marker.exists()
    assert ("adw_modules.quality" in sys.modules) == before
