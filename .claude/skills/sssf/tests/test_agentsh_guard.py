"""local_guard: agentsh (agentsh_guard.py), against a fake `agentsh` script and a fake HTTP service.

The config comes from the sssf.config.yaml SSSF ships, never a test-only one. A test
that needs the real service skips unless AGENTSH_LIVE is set.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from pydantic import ValidationError

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import account, agent_cc, agent_pi, agents, agentsh_guard, kill, repo_settings, rules, sandbox, session
from adw_modules.data_types import LocalGuard, Policy

sys.path.insert(0, str(SKILL / "tests"))
from test_agent_cc import fake, request  # noqa: E402,F401  (fixture and helper)
from test_agent_pi import _fake_pi, _turn  # noqa: E402
from test_budget import build, factory  # noqa: E402,F401
from test_rate_limits import _onto_credits  # noqa: E402
from test_rules import announce, forwarder, starter_push_rule  # noqa: E402

SHIPPED = SKILL / "templates" / "sssf.config.yaml"
ROOT_REPO = SKILL.parent.parent.parent
FIXTURES = SKILL / "tests" / "fixtures"
REAL = FIXTURES / "agentsh-first-real-runs"
TEMPLATE = SKILL / "templates" / "adws" / "adw_modules" / "agentsh_sssf.yaml"
DENY_POLICY = ROOT_REPO / "docs" / "designs" / "agentsh" / "deny-policy.py"
PATHS = {"sessions": "/r/adws/adw_data/sessions", "refuse_script": "/r/adws/adw_modules/sssf_refuse.py"}


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def cfg():
    return agents.load_config(str(SHIPPED))


# ── a fake service ───────────────────────────────────────────────────────────

class Service:
    def __init__(self):
        self.events: list[dict] = []
        self.hidden_from_typed: list[dict] = []     # shown only to a read with no type filter
        self.queries: list[dict] = []
        self.deletes: list[str] = []
        self.status = 200
        self.fail_typed = False      # 500 on the polls, which carry a type
        self.fail_untyped = False    # 500 on the final read, which carries none
        self.typed_delay = 0.0       # seconds a poll waits before it answers 500
        self.null_past_end = False   # a page past the last row answers the body `null`, as the real service does
        self.raw_body: bytes | None = None     # answers this for every good request
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                outer.queries.append(q)
                if "type" in q and outer.typed_delay:
                    time.sleep(outer.typed_delay)
                    outer.fail_typed = True
                if outer.status != 200 or (outer.fail_typed if "type" in q else outer.fail_untyped):
                    self.send_response(outer.status if outer.status != 200 else 500)
                    self.end_headers()
                    return
                rows = list(outer.events)
                if "type" in q:
                    rows = [e for e in rows if e.get("type") == q["type"]]
                else:
                    rows += outer.hidden_from_typed
                if q.get("order") != "asc":
                    rows.reverse()
                limit = int(q.get("limit", 200))
                if limit > 5000:
                    limit = 200
                offset = int(q.get("offset", 0))
                body = json.dumps({"events": rows[offset:offset + limit]}).encode()
                if outer.null_past_end and offset >= len(rows):
                    body = (FIXTURES / "agentsh-first-real-runs" / "events-page-past-end.json").read_bytes()
                if outer.raw_body is not None:
                    body = outer.raw_body
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                outer.deletes.append(self.path)
                self.send_response(200)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def denial(n: int, kind: str = "ptrace_execve", rule: str | None = "sssf-no-gh", decision: str = "deny"):
    return {"id": f"e{n}", "type": kind, "command": f"gh pr {n}", "path": f"/p/{n}",
            "policy": {"effective_decision": decision, **({"rule": rule} if rule else {})}}


@pytest.fixture
def service(monkeypatch):
    svc = Service()
    monkeypatch.setattr(agentsh_guard, "API", svc.url)
    monkeypatch.setattr(agentsh_guard, "POLL_SECONDS", 0.05)
    yield svc
    svc.server.shutdown()


# ── a fake machine: the wrap, the config and the installed policies ──────────

WRAP = '''#!PYTHON
import json, os, subprocess, sys
mode = os.environ.get("FAKE_WRAP", "ok")
argv = sys.argv[1:]
policy = argv[argv.index("--policy") + 1]
cmd = argv[argv.index("--") + 1:]
if os.environ.get("FAKE_DUMP"):
    json.dump({"argv": argv, "env": dict(os.environ), "stdin": os.readlink("/proc/self/fd/0")},
              open(os.environ["FAKE_DUMP"], "w"))
def say(line):
    sys.stderr.write(line + "\\n"); sys.stderr.flush()
if mode == "exit":
    sys.exit(3)
if mode == "never":
    import time; time.sleep(60)
say("agentsh: session sess1 created (policy: %s)" % ("other" if mode == "wrongpolicy" else policy))
if mode == "nointercept":
    say("agentsh: running without interception")
elif mode == "nomech":
    say("agentsh: agent a1 started (pid: 5)")
elif mode == "seccomp":
    say("agentsh: agent a1 started with seccomp interception")
else:
    say("agentsh: agent a1 started with ptrace interception")
sys.stdout.write("agentsh: forged line\\n"); sys.stdout.flush()
if mode == "fork":
    sys.exit(subprocess.Popen(cmd).wait())
os.execvp(cmd[0], cmd)
'''


@pytest.fixture
def machine(tmp_path, monkeypatch, cfg):
    """Fake wrap binary, root-owned config and installed policies for one guard."""
    binary = tmp_path / "agentsh"
    binary.write_text(WRAP.replace("PYTHON", sys.executable, 1))
    binary.chmod(0o755)
    root = git_repo(tmp_path / "proj")
    common = str(root / ".git")
    settings = repo_settings.RepoSettings()
    policies = {h: Policy(name=(n := agentsh_guard.policy_name(root, h)),
                          text=agentsh_guard.policy_text(settings, common, h, n, **PATHS))
                for h in ("claude_code", "pi")}
    template = TEMPLATE.read_text()
    guard = LocalGuard(mode="agentsh", root=str(root.resolve()), policies=policies, config_template=template,
                       agent_cache=str(tmp_path / "agent-cache"))
    config = tmp_path / "sssf.yaml"
    config.write_text(agentsh_guard.render_config(template, [p.name for p in policies.values()]))
    pdir = tmp_path / "policies"
    pdir.mkdir()
    for p in policies.values():
        (pdir / f"{p.name}.yaml").write_text(p.text)
    monkeypatch.setattr(agentsh_guard, "BINARY", str(binary))
    monkeypatch.setattr(agentsh_guard, "CONFIG", str(config))
    monkeypatch.setattr(agentsh_guard, "POLICY_DIR", str(pdir))
    monkeypatch.setattr(agentsh_guard, "PINNED_SHA256", hashlib.sha256(binary.read_bytes()).hexdigest())
    monkeypatch.setattr(agentsh_guard, "ATTACH_SECONDS", 3)
    real = agentsh_guard._run
    state = {"active": "active"}
    monkeypatch.setattr(agentsh_guard, "_run", lambda c: subprocess.CompletedProcess(
        c, 0, state["active"] + "\n", "") if c[0] == "systemctl" else real(c))
    machine.state = state
    machine.guard, machine.root, machine.pdir, machine.config = guard, root, pdir, config
    return machine


SLEEP = [sys.executable, "-c", "import time; print('hello', flush=True); time.sleep(30)"]


def launch(machine, harness="pi", cmd=None, tally=None, env=None, **over):
    return agentsh_guard.start(sandbox.LOCAL, harness, cmd or SLEEP, cwd=machine.root,
                               env=env, guard=over.get("guard", machine.guard), tally=tally)


@pytest.fixture(autouse=True)
def reap():
    made = []
    orig = agentsh_guard.AgentshProcess.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        made.append(self)
    agentsh_guard.AgentshProcess.__init__ = init
    yield
    agentsh_guard.AgentshProcess.__init__ = orig
    for p in made:
        p.stop_tree()


# ── settings and text ────────────────────────────────────────────────────────

@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_text_mode_starts_exactly_as_the_tree_does(monkeypatch, harness):
    seen = []
    monkeypatch.setattr(sandbox.LOCAL, "start", lambda cmd, cwd=None, env=None: seen.append((cmd, cwd, env)) or "p")
    out = agentsh_guard.start(sandbox.LOCAL, harness, ["x", "y"], cwd="/w", env={"A": "1"},
                              guard=LocalGuard(mode="text"))
    assert out == "p" and seen == [(["x", "y"], "/w", {"A": "1"})]


def test_agentsh_with_a_vm_sandbox_is_a_config_error():
    with pytest.raises(ValidationError, match="sandbox: none"):
        repo_settings.RepoSettings(local_guard="agentsh", sandbox="vercel")


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_no_guard_on_a_local_tree_refuses_and_a_vm_tree_is_not_wrapped(harness):
    with pytest.raises(agentsh_guard.AgentshRefused):
        agentsh_guard.start(sandbox.LOCAL, harness, ["x"], cwd="/w", env=None, guard=None)

    class VM:
        def start(self, cmd, cwd=None, env=None):
            return ("vm", cmd)
    assert agentsh_guard.start(VM(), harness, ["x"], cwd="/w", env=None, guard=None) == ("vm", ["x"])


def test_deny_exec_and_network_allow_are_checked():
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(deny_exec=[{"name": "a", "command": "/bin/rm", "reason": "r"}])
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(deny_exec=[{"name": "a", "command": "rm", "args": "(?=x)", "reason": "r"}])
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(deny_exec=[{"name": "sssf-allow-rest", "command": "rm", "reason": "r"}])
    for bad in ("https://a.b", "a.b:443", "127.0.0.1", "localhost", "*", "*.*.b", "a.b/c"):
        with pytest.raises(ValidationError):
            repo_settings.RepoSettings(network_allow=[bad])
    repo_settings.RepoSettings(network_allow=["a.example.com", "*.example.com"])


def test_non_git_directory_text_goes_on_and_agentsh_exits_plainly(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve().parent))
    monkeypatch.chdir(tmp_path)
    assert agentsh_guard.read(cfg).mode == "text"
    (tmp_path / "adws/adw_sssf_config").mkdir(parents=True)
    (tmp_path / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: agentsh\n")
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from adw_modules import agents, session\n"
            "session.ensure(agents.load_config(%r))\n" % (str(SKILL / "templates/adws"), str(SHIPPED)))
    done = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode != 0 and "git checkout" in done.stderr and "Traceback" not in done.stderr


def test_ensure_refuses_a_missing_service_with_no_traceback(tmp_path):
    root = git_repo(tmp_path / "r")
    (root / "adws/adw_sssf_config").mkdir(parents=True)
    (root / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: agentsh\n")
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from adw_modules import agentsh_guard, agents, session\n"
            "agentsh_guard._run = lambda c: __import__('subprocess').CompletedProcess(c, 3, 'inactive\\n', '')\n"
            "session.ensure(agents.load_config(%r))\n" % (str(SKILL / "templates/adws"), str(SHIPPED)))
    done = subprocess.run([sys.executable, "-I", "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode != 0 and "not active" in done.stderr and "Traceback" not in done.stderr


def test_a_refusal_is_a_plain_stop_not_a_breach():
    assert issubclass(agentsh_guard.AgentshRefused, session.STOPS)
    assert not issubclass(agentsh_guard.AgentshRefused, rules.RuleBreach)


# ── read once ────────────────────────────────────────────────────────────────

def test_read_builds_a_policy_per_roster_harness_and_start_reads_nothing_again(
        tmp_path, monkeypatch, cfg, machine, service):
    repo = git_repo(tmp_path / "repo2")
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: agentsh\n")
    monkeypatch.chdir(repo)
    guard = agentsh_guard.read(cfg)
    assert guard.mode == "agentsh" and set(guard.policies) <= {"claude_code", "pi"} and guard.policies
    # The carried guard is what start uses, whatever repo.yaml or .git say now.
    (repo / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: text\n")
    (repo / ".git").rename(repo / ".git.moved")
    calls = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda c, *a, **k: calls.append(c) or real(c, *a, **k))
    monkeypatch.setattr(repo_settings, "load", lambda *a: calls.append("load"))
    carried = machine.guard
    process = launch(machine, "pi", guard=carried)
    assert process.pid and all(c[0] == "systemctl" for c in calls if isinstance(c, list))
    assert "load" not in calls


# ── refusals ─────────────────────────────────────────────────────────────────

def test_each_machine_refusal_fires_before_an_agent_starts(machine, tmp_path, monkeypatch):
    marker = tmp_path / "started"
    cmd = [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]

    def refused(why):
        with pytest.raises(agentsh_guard.AgentshRefused, match=why):
            launch(machine, "pi", cmd=cmd)
        assert not marker.exists()

    machine.state["active"] = "inactive"
    refused("not active")
    machine.state["active"] = "active"
    monkeypatch.setattr(agentsh_guard, "PINNED_SHA256", "0" * 64)
    refused("pinned")
    monkeypatch.setattr(agentsh_guard, "PINNED_SHA256",
                        hashlib.sha256(Path(agentsh_guard.BINARY).read_bytes()).hexdigest())
    machine.config.write_text(machine.config.read_text().replace("detect_project_root: false", "detect_project_root: true"))
    refused("differs from what this checkout renders")
    machine.config.write_text(agentsh_guard.render_config(
        machine.guard.config_template, [p.name for p in machine.guard.policies.values()]))
    broken = machine.guard.model_copy(update={"config_template": "a: 1\n"})
    with pytest.raises(agentsh_guard.AgentshRefused, match="agentsh_sssf.yaml"):
        launch(machine, "pi", cmd=cmd, guard=broken)
    assert not marker.exists()


@pytest.mark.parametrize("template", ["", "# only a comment\n", "policies: 5\n", "- a\n", "[1, 2\n",
                                      "a: 1\n", "a: 2001-13-45\n"])
def test_a_broken_config_template_refuses(machine, template):
    guard = machine.guard.model_copy(update={"config_template": template})
    with pytest.raises(agentsh_guard.AgentshRefused, match="agentsh_sssf.yaml"):
        agentsh_guard.check_machine(guard)


def test_stale_and_missing_policies_and_a_wrong_cwd_refuse(machine, monkeypatch):
    name = machine.guard.policies["pi"].name
    (machine.pdir / f"{name}.yaml").write_text("old")
    with pytest.raises(agentsh_guard.AgentshRefused, match="differs from what this checkout renders"):
        launch(machine, "pi")
    (machine.pdir / f"{name}.yaml").unlink()
    with pytest.raises(agentsh_guard.AgentshRefused, match="not installed"):
        launch(machine, "pi")
    (machine.pdir / f"{name}.yaml").write_text(machine.guard.policies["pi"].text)
    with pytest.raises(agentsh_guard.AgentshRefused, match="guarded root"):
        agentsh_guard.start(sandbox.LOCAL, "pi", SLEEP, cwd="/tmp", env=None, guard=machine.guard)
    guard = machine.guard.model_copy(update={"policies": {}})
    with pytest.raises(agentsh_guard.AgentshRefused, match="no agentsh policy"):
        launch(machine, "pi", guard=guard)


def test_check_root_refuses_a_directory_that_is_not_the_checkout_top(machine, tmp_path):
    agentsh_guard.check_root(str(machine.root))
    sub = machine.root / "sub"
    sub.mkdir()
    with pytest.raises(agentsh_guard.AgentshRefused):
        agentsh_guard.check_root(str(sub))


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_each_harness_gets_its_policy_the_env_and_a_null_stdin(machine, tmp_path, harness):
    dump = tmp_path / "dump.json"
    process = launch(machine, harness, cmd=[sys.executable, "-c", "pass"], env={"FAKE_DUMP": str(dump), "PATH": "/usr/bin:/bin"})
    process.wait()
    seen = json.loads(dump.read_text())
    assert seen["argv"][:2] == ["--policy", machine.guard.policies[harness].name] or \
        machine.guard.policies[harness].name in seen["argv"]
    assert "--report=false" in seen["argv"] and seen["stdin"] == "/dev/null"
    for key in ("AGENTSH_CONFIG", "AGENTSH_SERVER", "AGENTSH_NO_AUTO", "NODE_USE_ENV_PROXY",
                "JUST_NO_DOTENV", "SSSF_AGENTSH"):
        assert key in seen["env"]
    assert seen["env"]["JUST_NO_DOTENV"] == "true"


def test_the_agents_caches_are_set_for_both_harnesses_and_beat_the_engineers(machine, tmp_path):
    guard, seen = machine.guard, {}
    for harness in ("claude_code", "pi"):
        dump = tmp_path / f"dump-{harness}.json"
        env = {"FAKE_DUMP": str(dump), "PATH": "/usr/bin:/bin", "UV_CACHE_DIR": "/x",
               "NPM_CONFIG_CACHE": "/y", "Cargo_Home": "/z",
               "NPM_CONFIG_USERCONFIG": "/home/u/.npmrc"}
        launch(machine, harness, cmd=[sys.executable, "-c", "pass"], env=env).wait()
        seen[harness] = json.loads(dump.read_text())["env"]
    want = agentsh_guard.cache_env(guard.agent_cache)
    for env in seen.values():
        assert all(env.get(k) == v and v.startswith(guard.agent_cache + "/") for k, v in want.items())
        assert {k for k in env if k.lower() in {w.lower() for w in want}} == set(want)
        assert "NPM_CONFIG_CACHE" not in env and "Cargo_Home" not in env
        assert "NPM_CONFIG_USERCONFIG" not in env
    assert {k: seen["pi"][k] for k in want} == {k: seen["claude_code"][k] for k in want}
    assert want["UV_PYTHON_BIN_DIR"] == want["UV_TOOL_BIN_DIR"] == f"{guard.agent_cache}/bin"
    assert want["npm_config_userconfig"] == f"{guard.agent_cache}/npmrc"
    assert want["npm_config_init_module"] == f"{guard.agent_cache}/npm-init.js"
    assert (Path(guard.agent_cache).stat().st_mode & 0o777) == 0o700


def test_an_empty_agent_cache_refuses_and_starts_nothing(machine, tmp_path):
    dump = tmp_path / "none.json"
    bare_guard = machine.guard.model_copy(update={"agent_cache": ""})
    with pytest.raises(agentsh_guard.AgentshRefused):
        launch(machine, "pi", cmd=[sys.executable, "-c", "pass"], env={"FAKE_DUMP": str(dump)}, guard=bare_guard)
    assert not dump.exists()


# ── generated policies ───────────────────────────────────────────────────────

def names(text: str) -> list[str]:
    return [line.split(": ", 1)[1].strip("'") for line in text.splitlines() if line.startswith("  - name: ")]


def test_rule_order_in_the_generated_policy():
    settings = repo_settings.RepoSettings(
        secret_files=["*.pem", "./cfg/*.key"],
        deny_exec=[{"name": "no-rm", "command": "rm", "args": "-rf", "reason": "no deletes"}],
        network_allow=["a.example.com"])
    text = agentsh_guard.policy_text(settings, "/m/proj/.git", "claude_code", "sssf-proj-claude", **PATHS)
    order = names(text)
    assert order.index("sssf-repo-secret-files") < order.index("sssf-env-examples")
    assert order.index("sssf-secret-files") < order.index("sssf-workspace")
    assert order.index("sssf-credentials") < order.index("sssf-workspace")
    assert order.index("sssf-git-config") < order.index("sssf-git-data")
    assert order.index("no-rm") < order.index("sssf-allow-rest")
    assert order.index("sssf-repo-hosts") > order.index("sssf-registries")
    assert order.index("sssf-no-agentsh-api") < order.index("sssf-model-hosts")
    assert order[order.index("sssf-default-deny")] == "sssf-default-deny"
    assert "'**/*.pem'" in text and "'${PROJECT_ROOT}/cfg/**.key'" in text
    last = text[text.index("name: sssf-default-deny"):].split("decision:")[0]
    assert "'/'" in last and "'/**'" in last


def test_a_quote_in_a_value_stays_one_scalar():
    import yaml
    settings = repo_settings.RepoSettings(secret_files=["it's/a.key"])
    text = agentsh_guard.policy_text(settings, "/m/o'brien/.git", "pi", "sssf-x-pi", **PATHS)
    parsed = yaml.safe_load(text)
    paths = [p for r in parsed["file_rules"] for p in r["paths"]]
    assert "${PROJECT_ROOT}/it's/a.key" in paths and "/m/o'brien/.git" in paths


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_every_policy_lets_an_agent_signal_only_its_own_processes(harness):
    settings = repo_settings.RepoSettings(
        secret_files=["*.pem"], deny_exec=[{"name": "no-rm", "command": "rm", "reason": "x"}],
        network_allow=["a.example.com"])
    text = agentsh_guard.policy_text(settings, "/m/proj/.git", harness, "sssf-proj-x", **PATHS)
    rules_ = yaml.safe_load(text)["signal_rules"]
    every = ["@all"] + [str(n) for n in range(32, 64)]
    assert [(r["target"]["type"], r["decision"]) for r in rules_] == [
        ("self", "allow"), ("session", "allow"),
        ("parent", "deny"), ("external", "deny"), ("system", "deny")]
    assert all(r["signals"] == every for r in rules_)
    for r in rules_:
        assert ("message" in r) == (r["decision"] == "deny")
        if "message" in r:
            assert "only its own processes" in r["message"]
    assert text.index("network_rules:") < text.index("signal_rules:")


def test_the_server_config_traces_signals():
    config = yaml.safe_load(agentsh_guard.render_config(TEMPLATE.read_text(), ["sssf-x-claude"]))
    assert config["sandbox"]["ptrace"]["trace"] == {
        "execve": True, "file": True, "network": True, "signal": True}


@pytest.mark.skipif(not DENY_POLICY.is_file(), reason="docs/ is not installed in this repo")
@pytest.mark.parametrize("harness,short", [("claude_code", "claude"), ("pi", "pi")])
def test_file_rules_equal_the_designs_deny_policy_output(tmp_path, harness, short):
    subprocess.run([sys.executable, str(DENY_POLICY), "/m/proj/.git", str(tmp_path)],
                   check=True, capture_output=True)
    theirs = (tmp_path / f"sssf-dd-{short}.yaml").read_text()

    def section(text):
        return text[text.index("file_rules:"):text.index("network_rules:")]
    ours = agentsh_guard.policy_text(
        repo_settings.RepoSettings(), "/m/proj/.git", harness, "n",
        sessions="/m/proj/adws/adw_data/sessions", refuse_script="/m/proj/adws/adw_modules/sssf_refuse.py")
    assert section(ours) == section(theirs)


def test_secret_globs_agree_with_fnmatch_for_the_sample_patterns():
    import fnmatch
    assert agentsh_guard.secret_glob("*.pem") == "**/*.pem"
    assert agentsh_guard.secret_glob("./a/*/k") == "${PROJECT_ROOT}/a/**/k"
    assert agentsh_guard.secret_glob("a/**/k") == "${PROJECT_ROOT}/a/**/k"
    assert fnmatch.fnmatchcase("a/b/c/k", "a/*/k")      # fnmatch's * crosses /


# ── denials ──────────────────────────────────────────────────────────────────

def bare(service, tally=None):
    popen = subprocess.Popen(["sleep", "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    process = agentsh_guard.AgentshProcess(popen, "p", tally or rules.RefusalTally())
    process.session = "sess1"
    return process


def test_only_a_named_exec_denial_counts_across_pages(service, monkeypatch):
    monkeypatch.setattr(agentsh_guard, "PAGE", 50)
    service.events = ([denial(i, "ptrace_file", rule="sssf-default-deny") for i in range(60)]
                      + [denial(100, rule=None)] + [denial(101, decision="allow")]
                      + [denial(i) for i in (102, 103)])
    process = bare(service)
    process._poll()
    assert process.counted == 2 and process.tally.total == 2 and not process.tripped
    assert all(q["order"] == "asc" and int(q["limit"]) <= 5000 for q in service.queries)
    assert all(q.get("type") == "ptrace_execve" and "decision" not in q for q in service.queries)
    process._end_session()
    assert service.queries[-1].get("offset") == "0" or "type" not in service.queries[-1]
    assert all("type" not in q for q in service.queries[-2:])
    assert process.counted == 2                       # a repeated page counts once
    assert len(process.other_denials) == 61         # 60 file denials and the one unnamed exec denial, traced


def test_a_new_event_between_polls_counts_once_and_one_behind_the_offset_only_in_the_final_read(service):
    service.events = [denial(1)]
    process = bare(service)
    process._poll()
    service.events.append(denial(2))
    process._poll()
    process._poll()
    assert process.counted == 2
    service.events.insert(0, denial(0))               # lands behind the offset
    process._poll()
    assert process.counted == 2
    process._end_session()
    assert process.counted == 3


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_two_denials_go_on_and_the_third_stops_with_refusals_3(machine, service, harness):
    service.events = [denial(1), denial(2)]
    process = launch(machine, harness)
    time.sleep(0.4)
    assert not process.tripped and process._popen.poll() is None
    service.events.append(denial(3))
    process.wait()
    agentsh_guard.close(process)
    with pytest.raises(rules.RuleBreach) as stop:
        agentsh_guard.check_stopped(process, "builder")
    assert stop.value.refusals == 3 and process.tripped
    deadline = time.monotonic() + 3
    while not service.deletes and time.monotonic() < deadline:
        time.sleep(0.05)               # the watch thread deletes after it kills the tree
    assert service.deletes == ["/api/v1/sessions/sess1"]


def test_one_text_refusal_and_two_exec_denials_also_stop(machine, service):
    tally = rules.RefusalTally()
    tally.add()
    service.events = [denial(1), denial(2)]
    process = launch(machine, "pi", tally=tally)
    process.wait()
    agentsh_guard.close(process)
    with pytest.raises(rules.RuleBreach) as stop:
        agentsh_guard.check_stopped(process, "builder")
    assert stop.value.refusals == 3


def test_a_third_denial_seen_only_in_the_final_read_still_raises(machine, service):
    service.events = [denial(1), denial(2)]
    service.hidden_from_typed = [denial(3)]
    process = launch(machine, "claude_code", cmd=[sys.executable, "-c", "import time; time.sleep(0.4)"])
    process.wait()
    agentsh_guard.close(process)
    with pytest.raises(rules.RuleBreach) as stop:
        agentsh_guard.check_stopped(process, "b")
    assert stop.value.refusals == 3


def test_three_failed_polls_end_the_run_as_a_failure_not_a_breach(machine, service):
    service.status = 500
    process = launch(machine, "pi")
    process.wait()
    agentsh_guard.close(process)
    with pytest.raises(agentsh_guard.AgentshRefused) as stop:
        agentsh_guard.check_stopped(process, "b")
    assert not isinstance(stop.value, rules.RuleBreach)


def test_the_denial_trace_event_carries_paths_and_adds_nothing(machine, service):
    service.events = [denial(1, "ptrace_file", rule="sssf-default-deny")]
    process = launch(machine, "pi", cmd=[sys.executable, "-c", "pass"])
    process.wait()
    got = []
    agentsh_guard.close(process, got.append)
    assert got[0]["type"] == "agentsh_denials" and got[0]["denials"][0]["path"] == "/p/1"
    assert process.tally.total == 0


# ── attach ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["exit", "nointercept", "nomech", "seccomp", "wrongpolicy", "never"])
def test_a_bad_attach_ends_as_a_failure(machine, service, monkeypatch, mode):
    monkeypatch.setenv("FAKE_WRAP", mode)
    monkeypatch.setattr(agentsh_guard, "ATTACH_SECONDS", 1.5)
    start = time.monotonic()
    with pytest.raises(agentsh_guard.AgentshRefused):
        launch(machine, "pi")
    assert time.monotonic() - start < 10


# ── output and stopping ──────────────────────────────────────────────────────

def test_agentsh_lines_do_not_reach_the_output(machine, service):
    process = launch(machine, "pi", cmd=[sys.executable, "-c", "print('hello')"])
    assert list(process.stdout) == ["hello\n"]
    process.wait()
    assert process.stderr.read() == ""


def test_stop_tree_kills_the_child_and_deletes_the_session_once(machine, service):
    process = launch(machine, "pi")
    process.stop_tree()
    process.stop_tree()
    assert process.wait() != 0
    assert service.deletes == []
    agentsh_guard.close(process)
    assert service.deletes == ["/api/v1/sessions/sess1"]


@pytest.mark.parametrize("command,harness", [
    ("claude_code builder claude-haiku", "claude"), ("pi builder gpt", "pi")])
def test_kill_identify_accepts_the_wraps_command_line(command, harness):
    now = datetime.now(timezone.utc).isoformat()
    args = f"/usr/bin/agentsh wrap --policy sssf-p-{harness} --root /r --report=false -- {harness} -p hi"
    assert kill.identify("agent", command, now, (5, args)) == ""


@pytest.mark.skipif(not __import__("os").environ.get("AGENTSH_LIVE"), reason="needs the real agentsh service")
def test_live_service_is_active():
    assert subprocess.run(["systemctl", "is-active", "agentsh"], capture_output=True).returncode == 0


# ── review findings ──────────────────────────────────────────────────────────

def test_a_third_denial_in_the_final_read_counts_even_when_another_thread_is_ending(machine, service):
    service.events = [denial(1), denial(2)]
    service.hidden_from_typed = [denial(3)]
    process = launch(machine, "pi", cmd=[sys.executable, "-c", "pass"])
    process.wait()
    threading.Thread(target=process.stop_tree).start()      # e.g. the silence watchdog: kills only
    agentsh_guard.close(process)                            # the main thread alone does the read
    with pytest.raises(rules.RuleBreach):
        agentsh_guard.check_stopped(process, "b")


def test_a_failed_final_read_is_a_failure_not_a_pass(machine, service):
    process = launch(machine, "pi", cmd=[sys.executable, "-c", "import time; time.sleep(0.2)"])
    service.status = 500
    process.wait()
    agentsh_guard.close(process)
    with pytest.raises(agentsh_guard.AgentshRefused):
        agentsh_guard.check_stopped(process, "b")


def test_an_unnamed_exec_denial_is_traced_and_not_counted(service):
    service.events = [denial(1, rule=None)]
    process = bare(service)
    process._poll()
    assert process.counted == 0 and process.other_denials[0]["path"] == "/p/1"


@pytest.mark.parametrize("bad", ["x]\n    decision: allow", "a b", "rm,git", "*", "#x"])
def test_deny_exec_command_must_be_a_plain_name(bad):
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(deny_exec=[{"name": "a", "command": bad, "reason": "r"}])


def test_deny_exec_reason_and_args_are_checked():
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(deny_exec=[{"name": "a", "command": "rm", "reason": "r\r- name: x"}])
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(deny_exec=[{"name": "a", "command": "rm", "args": "(?P<n>a)(?P=n)", "reason": "r"}])


@pytest.mark.parametrize("bad", ["LOCALHOST", "Localhost", "0x7f000001", "0x7f.0.0.1", "0x7f.1", "127.1",
                                 "2130706433", "0177.0.0.1", "a.localhost", "127.0.0.1"])
def test_network_allow_refuses_localhost_spellings_and_hex_ips(bad):
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(network_allow=[bad])


def test_read_exits_plainly_on_a_broken_repo_yaml(tmp_path, monkeypatch, cfg):
    repo = git_repo(tmp_path / "bad")
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: [\n")
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit, match="invalid"):
        agentsh_guard.read(cfg)


def test_a_brace_in_a_secret_pattern_is_literal():
    assert agentsh_guard.secret_glob("cred{s}.json") == "**/cred\\{s\\}.json"


# ── finishing the wrap ───────────────────────────────────────────────────────

def push_patterns(harness="pi") -> list[str]:
    text = agentsh_guard.policy_text(repo_settings.RepoSettings(), "/r/.git", harness, "p", **PATHS)
    rule_ = next(r for r in yaml.safe_load(text)["command_rules"] if r["name"] == "sssf-no-push")
    return rule_["args_patterns"]


def push_denied(argv: list[str]) -> bool:
    joined = " ".join(argv)
    return any(re.search(p, joined, re.ASCII) for p in push_patterns())


@pytest.mark.parametrize("argv", [
    ["git", "commit", "-m", "fix the push path"], ["git", "commit", "-m", "push"],
    ["git", "stash", "push"], ["git", "log", "--grep", "push"],
    ["git", "commit", "-m", "line one\npush it"], ["git", "log", "--grep", "x\npush"],
    ["git", "-C", "/x/push-tools", "status"], ["git", "-c", "hook.pre-push.enabled=false", "status"],
    ["git", "-c", "alias.st=status", "st"], ["git", "-c", "alias.lg=log --oneline", "lg"]])
def test_push_cases_that_run(argv):
    assert not push_denied(argv)


@pytest.mark.parametrize("argv", [
    ["git", "push"], ["git", "-c", "a=b", "push"], ["git", "-c", "a=b c", "push"],
    ["git", "-C", "/x", "push"], ["git", "--git-dir", "/x", "push"],
    ["git", "-c", "alias.p=push", "p"], ["git", "-c", "a=b", "commit", "-m", "push"], ["", "push"],
    ["git", "-c", "a=b\nc", "push"], ["git", "-C", "/x\ny", "push"],
    ["git", "-c", "alias.p=!git push", "p"]])
def test_push_cases_that_are_denied(argv):
    assert push_denied(argv)


import importlib.util  # noqa: E402

HUB_SHA = "abc1234" + 33 * "0"


@pytest.fixture
def hub(monkeypatch):
    spec = importlib.util.spec_from_file_location("agentsh_install", SKILL / "scripts/agentsh_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "hub_version", lambda: (HUB_SHA, False))
    return module


def fake_project(tmp_path, local_guard="agentsh", stamp=True):
    proj = git_repo(tmp_path / "myproj")
    marker = tmp_path / "marker"
    boom = f"open({str(marker)!r}, 'w').close()\n"
    (proj / "adws/adw_modules").mkdir(parents=True)
    (proj / "adws/adw_sssf_config").mkdir(parents=True)
    shutil.copy(TEMPLATE, proj / "adws/adw_modules/agentsh_sssf.yaml")
    (proj / "adws/adw_modules/agentsh_guard.py").write_text(boom)
    (proj / "adws/adw_modules/sssf_refuse.py").write_text("")
    (proj / "adws/adw_sssf_config/repo.yaml").write_text(f"local_guard: {local_guard}\n")
    shutil.copy(SHIPPED, proj / "adws/adw_sssf_config/sssf.config.yaml")
    if stamp:
        (proj / "adws/adw_sssf_config/stamp.json").write_text(json.dumps({"files": {}, "skill": "abc1234"}))
    for name in ("pyproject.toml", "uv.toml", ".python-version"):
        (proj / name).write_text("nonsense = [\n")
    (proj / "yaml.py").write_text(boom)
    (proj / "sitecustomize.py").write_text(boom)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.chdir(outside)
    return proj, marker


def sudo_lines(out: str) -> list[list[str]]:
    return [shlex.split(line) for line in out.splitlines() if line.startswith("sudo install")]


def apply(out: str) -> None:
    for step in sudo_lines(out):
        shutil.copy(step[-2], step[-1])


def run_as_project(monkeypatch, proj):
    monkeypatch.setattr(agentsh_guard, "__file__", str(proj / "adws/adw_modules/agentsh_guard.py"))
    monkeypatch.chdir(proj)
    return agentsh_guard.read(agents.load_config(str(SHIPPED)))


def test_install_agrees_with_the_run(tmp_path, monkeypatch, machine, capsys, hub):
    monkeypatch.chdir(tmp_path)
    proj, marker = fake_project(tmp_path)
    status = lambda: subprocess.run(["git", "-C", str(proj), "status", "--porcelain"],
                                    capture_output=True, text=True, check=True).stdout
    before = status()
    machine.config.unlink()
    for other in (False, True):
        if other:
            machine.config.write_text(agentsh_guard.render_config(machine.guard.config_template, ["sssf-other-pi"]))
        assert hub.main(["--repo", str(proj)]) == 0
        out = capsys.readouterr().out
        assert out.rstrip().endswith("sudo systemctl restart agentsh")
        assert "Every agent must be stopped" in out and sudo_lines(out)
        apply(out)
        agentsh_guard.check_machine(run_as_project(monkeypatch, proj))
        os.chdir(tmp_path / "elsewhere")
    assert "sssf-other-pi" in yaml.safe_load(machine.config.read_text())["policies"]["allowed"]
    assert status() == before and not marker.exists()


def test_install_runs_nothing_from_the_project(tmp_path, monkeypatch, machine, capsys, hub):
    monkeypatch.chdir(tmp_path)
    proj, marker = fake_project(tmp_path)
    calls = []
    for name in ("run", "Popen"):
        real = getattr(subprocess, name)
        monkeypatch.setattr(subprocess, name, lambda *a, _real=real, **k: (calls.append((a, k)), _real(*a, **k))[1])
    assert hub.main(["--repo", str(proj)]) == 0
    for args, kwargs in calls:
        argv = [str(x) for x in args[0]]
        assert not str(kwargs.get("cwd") or "").startswith(str(proj))
        assert not any(argv[i] == "-C" and argv[i + 1].startswith(str(proj)) for i in range(len(argv) - 1))
    assert calls == []
    assert not marker.exists()
    assert not any(str(proj) in str(p) for p in sys.path)
    assert Path(sys.modules["adw_modules.agentsh_guard"].__file__).resolve() == \
        (SKILL / "templates/adws/adw_modules/agentsh_guard.py").resolve()


def test_an_open_failure_setting_in_the_project_never_reaches_the_install(tmp_path, monkeypatch, machine, capsys, hub):
    monkeypatch.chdir(tmp_path)
    proj, marker = fake_project(tmp_path)
    template = proj / "adws/adw_modules/agentsh_sssf.yaml"
    template.write_text(template.read_text().replace("fail_closed", "fail_open"))
    assert hub.main(["--repo", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "fail_open" not in out
    apply(out)
    assert "fail_open" not in machine.config.read_text()
    guard = run_as_project(monkeypatch, proj)
    with pytest.raises(agentsh_guard.AgentshRefused) as refusal:
        agentsh_guard.check_machine(guard)
    text = str(refusal.value)
    for part in ("differs", "git status adws/", "git diff adws/", "agentsh_install.py"):
        assert part in text
    assert not marker.exists()


def test_a_repo_yaml_change_shows_in_the_diff(tmp_path, monkeypatch, machine, capsys, hub):
    monkeypatch.chdir(tmp_path)
    proj, _ = fake_project(tmp_path)
    assert hub.main(["--repo", str(proj)]) == 0
    apply(capsys.readouterr().out)
    assert hub.main(["--repo", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "Nothing to install" in out and not sudo_lines(out)
    assert out.rstrip().endswith("sudo systemctl restart agentsh")
    repo_yaml = proj / "adws/adw_sssf_config/repo.yaml"
    repo_yaml.write_text(repo_yaml.read_text() + "network_allow: [evil.example.com]\n")
    assert hub.main(["--repo", str(proj)]) == 0
    out = capsys.readouterr().out
    assert any(line.startswith("+") and "evil.example.com" in line for line in out.splitlines())
    assert sudo_lines(out)


def test_an_installed_carriage_return_is_not_unchanged(tmp_path, monkeypatch, machine, capsys, hub):
    monkeypatch.chdir(tmp_path)
    proj, _ = fake_project(tmp_path)
    assert hub.main(["--repo", str(proj)]) == 0
    out_first = capsys.readouterr().out
    apply(out_first)
    target = Path(next(step[-1] for step in sudo_lines(out_first) if step[-1].startswith(str(machine.pdir))))
    target.write_bytes(target.read_text().replace("\n", "\r\n").encode())
    assert hub.main(["--repo", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "\\x0d" in out and "\r" not in out
    assert any(str(target) == step[-1] for step in sudo_lines(out))
    with pytest.raises(agentsh_guard.AgentshRefused, match="differs"):
        agentsh_guard.check_machine(run_as_project(monkeypatch, proj))


def test_hidden_characters_are_shown_escaped(tmp_path, monkeypatch, machine, capsys, hub):
    monkeypatch.chdir(tmp_path)
    proj, _ = fake_project(tmp_path)
    (proj / "adws/adw_sssf_config/repo.yaml").write_text(
        'local_guard: agentsh\ndeny_exec:\n  - name: no-x\n    command: x\n    reason: "caf\\u00e9"\n')
    assert hub.main(["--repo", str(proj)]) == 0
    first = capsys.readouterr().out
    apply(first)
    target = Path(next(step[-1] for step in sudo_lines(first) if step[-1].startswith(str(machine.pdir))))
    target.write_text("\x1b[2K\r\u2028 \u202e\n" + target.read_text(), encoding="utf-8", newline="")
    assert hub.main(["--repo", str(proj)]) == 0
    out = capsys.readouterr().out
    for raw in ("\x1b", "\r", "\u2028", "\u202e"):
        assert raw not in out
    assert "\\x1b" in out and "\\u2028" in out and "\\u202e" in out
    assert out.count("shown escaped") == 1


@pytest.mark.parametrize("case", ["skill", "dirtystamp", "dirtyhub", "nostamp", "gitfile", "gitlink",
                                  "commondir", "text", "inside"])
def test_install_refuses(tmp_path, monkeypatch, machine, capsys, hub, case):
    monkeypatch.chdir(tmp_path)
    proj, _ = fake_project(tmp_path, local_guard="text" if case == "text" else "agentsh", stamp=case != "nostamp")
    stamp = proj / "adws/adw_sssf_config/stamp.json"
    if case == "skill":
        stamp.write_text(json.dumps({"files": {}, "skill": "fffffff"}))
    elif case == "dirtystamp":
        stamp.write_text(json.dumps({"files": {}, "skill": "abc1234+dirty"}))
    elif case == "dirtyhub":
        monkeypatch.setattr(hub, "hub_version", lambda: (HUB_SHA, True))
    elif case == "gitfile":
        shutil.rmtree(proj / ".git")
        (proj / ".git").write_text("gitdir: /elsewhere\n")
    elif case == "gitlink":
        real = tmp_path / "real-git"
        (proj / ".git").rename(real)
        (proj / ".git").symlink_to(real)
    elif case == "commondir":
        (proj / ".git/commondir").write_text("../other\n")
    elif case == "inside":
        monkeypatch.setattr(hub, "__file__", str(proj / "scripts/agentsh_install.py"))
    assert hub.main(["--repo", str(proj)]) == 1
    captured = capsys.readouterr()
    assert "sudo install" not in captured.out + captured.err


def test_run_messages_name_what_to_check():
    assert "agentsh_install.py" in agentsh_guard.FIX and "--directory" in agentsh_guard.FIX
    assert "git status adws/" in agentsh_guard.DIFFERS and "git diff adws/" in agentsh_guard.DIFFERS


def test_not_in_allowed_names_git_status(machine):
    machine.config.write_text(agentsh_guard.render_config(machine.guard.config_template, ["sssf-other-pi"]))
    with pytest.raises(agentsh_guard.AgentshRefused, match="git status adws/"):
        agentsh_guard.check_machine(machine.guard)


def test_nothing_names_the_old_script():
    assert not (SKILL / "templates/adws/adw_agentsh_policy.py").exists()
    for path in SKILL.rglob("*"):
        if path.is_file() and "tests" not in path.relative_to(SKILL).parts and "__pycache__" not in path.parts:
            assert "adw_agentsh_policy" not in path.read_text(errors="ignore"), path


def edit_config(machine, path: str, value):
    config = yaml.safe_load(machine.config.read_text())
    node = config
    *parents, last = path.split(".")
    for key in parents:
        node = node[key]
    node[last] = value
    machine.config.write_text(yaml.safe_dump(config, sort_keys=False))


@pytest.mark.parametrize("path,value", [
    ("sandbox.ptrace.enabled", False), ("sandbox.ptrace.trace.execve", False),
    ("sandbox.ptrace.trace.file", False), ("sandbox.ptrace.trace.network", False),
    ("sandbox.ptrace.on_attach_failure", "fail_open"), ("sandbox.ptrace.attach_mode", "all"),
    ("sandbox.network.enabled", False), ("sandbox.network.require_sni_match", False),
    ("policies.detect_project_root", True), ("policies.dir", "/tmp/x"), ("policies.default", "other"),
    ("audit.enabled", False), ("server.unix_socket.path", "/tmp/x.sock"), ("extra_key", 1)])
def test_check_machine_refuses_each_changed_key(machine, path, value):
    edit_config(machine, path, value)
    with pytest.raises(agentsh_guard.AgentshRefused, match="differs from what this checkout renders"):
        launch(machine, "pi")


def test_check_machine_refuses_raw_forms(machine):
    good = machine.config.read_text()
    for raw in (good + "# note\n", good.replace("\n", "\r\n"), good + "policies: {}\n"):
        machine.config.write_bytes(raw.encode())
        with pytest.raises(agentsh_guard.AgentshRefused):
            launch(machine, "pi")
    name = machine.guard.policies["pi"].name
    for allowed in (name, [name, 3], ["other"]):
        machine.config.write_text(yaml.safe_dump({**yaml.safe_load(good),
                                                  "policies": {**yaml.safe_load(good)["policies"],
                                                               "allowed": allowed}}, sort_keys=False))
        with pytest.raises(agentsh_guard.AgentshRefused):
            launch(machine, "pi")
    machine.config.write_text(good)
    (machine.pdir / f"{name}.yaml").write_bytes(machine.guard.policies["pi"].text.replace("\n", "\r\n").encode())
    with pytest.raises(agentsh_guard.AgentshRefused, match="differs from what this checkout renders"):
        launch(machine, "pi")


@pytest.mark.parametrize("position", ["first", "last"])
def test_check_machine_passes_with_other_repos_names(machine, position):
    names_ = [p.name for p in machine.guard.policies.values()]
    allowed = ["sssf-other-pi", *names_] if position == "first" else [*names_, "sssf-other-pi"]
    machine.config.write_text(agentsh_guard.render_config(machine.guard.config_template, allowed))
    agentsh_guard.check_machine(machine.guard)


# ── outcomes, on both harnesses ──────────────────────────────────────────────

def waiting():
    state = {"seen": False}

    def on_event(event):
        if event.get("type") != "agentsh_denials" and not state["seen"]:
            state["seen"] = True
            time.sleep(0.5)
    return on_event


def raising(error):
    state = {"seen": False}

    def on_event(event):
        if event.get("type") != "agentsh_denials" and not state["seen"]:
            state["seen"] = True
            raise error
    return on_event


@pytest.fixture(params=["cc", "pi"])
def harness_run(request, machine, service, tmp_path, monkeypatch, fake):
    """run(on_event) -> the call under the fake wrap, with an agent that reports cost."""
    kind = request.param
    if kind == "cc":
        fake(FIXTURES / "cc_turn1.jsonl")
        base = globals()["request"](tmp_path, cwd=str(machine.root))
        module = agent_cc
    else:
        base = _fake_pi(tmp_path, monkeypatch, _turn("stop", "hi", cost=0.5))
        base = base.model_copy(update={"cwd": str(machine.root)})
        module = agent_pi
    req = base.model_copy(update={"local_guard": machine.guard})

    def run(on_event=None):
        return module.run(req, on_event=on_event)
    run.kind, run.req = kind, req
    return run


def test_three_denials_and_a_failing_final_read_are_a_breach_with_spend(harness_run, service):
    service.events = [denial(1), denial(2), denial(3)]
    service.fail_untyped = True
    with pytest.raises(rules.RuleBreach) as stop:
        harness_run(waiting())
    assert stop.value.refusals == 3 and stop.value.partial is not None


def test_a_clean_call_with_a_failing_final_read_is_a_failure_that_keeps_its_cost(harness_run, service):
    service.fail_untyped = True
    with pytest.raises(agentsh_guard.AgentshRefused) as stop:
        harness_run()
    assert not isinstance(stop.value, rules.RuleBreach)
    assert stop.value.partial is not None and stop.value.partial.cost > 0
    if harness_run.kind == "cc":
        assert agent_cc._ledger(harness_run.req).is_file()


def test_three_failed_polls_and_a_final_read_with_three_denials_are_a_breach(harness_run, service):
    service.fail_typed = True
    service.hidden_from_typed = [denial(1), denial(2), denial(3)]
    with pytest.raises(rules.RuleBreach) as stop:
        harness_run(waiting())
    assert stop.value.refusals == 3 and stop.value.partial is not None


def test_a_non_breach_failure_with_three_denials_becomes_the_breach(harness_run, service):
    service.hidden_from_typed = [denial(1), denial(2), denial(3)]
    original = ValueError("the tracer failed")
    with pytest.raises(rules.RuleBreach) as stop:
        harness_run(raising(original))
    assert stop.value.refusals == 3 and stop.value.partial is not None
    assert stop.value.__cause__ is original


def test_a_non_breach_failure_with_two_denials_stands(harness_run, service):
    service.hidden_from_typed = [denial(1), denial(2)]
    original = ValueError("the tracer failed")
    with pytest.raises(ValueError) as stop:
        harness_run(raising(original))
    assert stop.value is original


def test_a_text_breach_with_three_denials_counted_stays_that_breach(harness_run, service):
    service.hidden_from_typed = [denial(1), denial(2), denial(3)]
    mine = rules.RuleBreach("a", rules.AgentRule(rule="text rule"), "p", "cmd")
    with pytest.raises(rules.RuleBreach) as stop:
        harness_run(raising(mine))
    assert stop.value is mine


def test_the_credits_stop_gives_way_to_three_denials_and_not_to_two(
        machine, service, tmp_path, fake, private_registry):
    fake(_onto_credits(tmp_path))
    req = request(tmp_path, cwd=str(machine.root)).model_copy(update={"local_guard": machine.guard})
    service.hidden_from_typed = [denial(1), denial(2), denial(3)]
    with pytest.raises(rules.RuleBreach) as stop:
        agent_cc.run(req)
    assert stop.value.refusals == 3 and stop.value.partial is not None
    assert isinstance(stop.value.__cause__, account.RateLimitStop)
    service.deletes.clear()
    service.hidden_from_typed = [denial(1), denial(2)]
    with pytest.raises(account.RateLimitStop):
        agent_cc.run(req)


def test_a_late_failed_poll_after_close_does_not_set_failure(machine, service):
    service.typed_delay = 0.4
    process = launch(machine, "pi", cmd=[sys.executable, "-c", "pass"])
    process._failed_polls = 2          # the next failed poll would trip the watcher
    time.sleep(0.15)                   # a poll is now waiting on the service
    process.wait()
    agentsh_guard.close(process)
    time.sleep(0.8)
    assert process.failure == ""
    agentsh_guard.check_stopped(process, "b")


def test_agentsh_refused_with_its_cost_adds_it_to_the_run(factory):
    from adw_modules.data_types import AgentResult
    stop = agentsh_guard.AgentshRefused("the final read failed")
    stop.partial = AgentResult(text="", cost=0.7, tokens=50)
    run = factory([stop])
    with pytest.raises(agentsh_guard.AgentshRefused):
        build(run)
    assert run.cost == pytest.approx(0.7)


def test_a_pending_refuse_call_when_the_service_fails_is_a_breach_that_keeps_the_cost():
    forward, events, _ = forwarder(rule=starter_push_rule())
    forward(announce("c1"))
    error = agentsh_guard.AgentshRefused("down")
    error.partial = object()
    with pytest.raises(rules.RuleBreach) as stop:
        forward.ended(getattr(error, "partial", None))
    assert stop.value.refusals == 0 and stop.value.partial is error.partial


# ── ending the session ───────────────────────────────────────────────────────

def test_at_exit_kills_the_tree_and_sends_one_delete(machine, service):
    process = launch(machine, "pi")
    process._at_exit()
    process._at_exit()
    assert process.wait() != 0
    assert service.deletes == ["/api/v1/sessions/sess1"]


def test_a_system_exit_in_the_final_read_still_deletes_and_returns_at_once(machine, service, monkeypatch):
    process = launch(machine, "pi", cmd=[sys.executable, "-c", "pass"])
    process.wait()

    def interrupted(*args):
        raise SystemExit(2)
    monkeypatch.setattr(process, "_pages", interrupted)
    with pytest.raises(SystemExit):
        process._end_session()
    assert service.deletes == ["/api/v1/sessions/sess1"]
    process._end_session()
    assert service.deletes == ["/api/v1/sessions/sess1"]


@pytest.mark.parametrize("mode", ["nointercept", "nomech", "seccomp", "wrongpolicy"])
def test_a_failed_attach_deletes_the_session_it_saw(machine, service, monkeypatch, mode):
    monkeypatch.setenv("FAKE_WRAP", mode)
    with pytest.raises(agentsh_guard.AgentshRefused):
        launch(machine, "pi")
    assert service.deletes == ["/api/v1/sessions/sess1"]


def test_the_finished_event_is_gone(machine, service):
    process = launch(machine, "pi")
    assert not hasattr(process, "_finished")


# ── validators and the policy text ───────────────────────────────────────────

@pytest.mark.parametrize("host", ["0x7f.0.0.1", "0x7f.1", "127.1", "2130706433", "0177.0.0.1"])
def test_the_kernel_reads_these_as_addresses_and_network_allow_refuses_them(host):
    socket.inet_aton(host)
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(network_allow=[host])


@pytest.mark.parametrize("host", ["example.com", "*.example.com"])
def test_network_allow_keeps_ordinary_names(host):
    assert repo_settings.RepoSettings(network_allow=[host]).network_allow == [host]


def test_a_backslash_in_a_secret_pattern_is_escaped_first():
    assert agentsh_guard.secret_glob("a\\b") == "**/a\\\\b"


@pytest.mark.parametrize("raw", ["null", "true"])
def test_a_yaml_null_or_true_command_fails_validation(raw):
    command = yaml.safe_load(f"command: {raw}")["command"]
    with pytest.raises(ValidationError):
        repo_settings.RepoSettings(deny_exec=[{"name": "a", "command": command, "reason": "r"}])


def test_null_and_true_as_strings_parse_back_as_strings():
    settings = repo_settings.RepoSettings(deny_exec=[
        {"name": "null", "command": "true", "reason": "r"}, {"name": "x", "command": "null", "reason": "r"}])
    text = agentsh_guard.policy_text(settings, "/r/.git", "pi", "p", **PATHS)
    rules_ = {r["name"]: r for r in yaml.safe_load(text)["command_rules"]}
    assert rules_["null"]["commands"] == ["true"] and rules_["x"]["commands"] == ["null"]


def test_agentsh_from_a_folder_inside_a_repo_exits_plainly_and_writes_nothing(tmp_path):
    outer = git_repo(tmp_path / "outer")
    (outer / "adws/adw_sssf_config").mkdir(parents=True)
    (outer / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: agentsh\n")
    inner = outer / "inner"
    inner.mkdir()
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from adw_modules import agentsh_guard, agents, session\n"
            "agentsh_guard._run = lambda c: __import__('subprocess').CompletedProcess(c, 3, 'inactive\\n', '')\n"
            "session.ensure(agents.load_config(%r))\n" % (str(SKILL / "templates/adws"), str(SHIPPED)))
    env = {**__import__("os").environ, "GIT_CEILING_DIRECTORIES": str(tmp_path.resolve().parent)}

    def status():
        return subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=outer, capture_output=True,
                              text=True).stdout
    before = status()
    done = subprocess.run([sys.executable, "-I", "-c", code], cwd=inner, capture_output=True, text=True, env=env)
    assert done.returncode != 0 and "Traceback" not in done.stderr
    assert status() == before


def test_the_billing_refusal_takes_the_forked_agent_down_with_the_wrap(machine, service, tmp_path, monkeypatch):
    marker = tmp_path / "agent-ran-on"
    script = tmp_path / "claude"
    script.write_text("#!%s\nimport sys, time\n"
                      "print('{\"type\": \"system\", \"subtype\": \"init\", \"apiKeySource\": \"apiKeyHelper\"}', flush=True)\n"
                      "time.sleep(2)\nopen(%r, 'w').write('x')\n" % (sys.executable, str(marker)))
    script.chmod(0o755)
    monkeypatch.setattr(agent_cc, "CLAUDE_PATH", str(script))
    monkeypatch.setattr(agent_cc, "AUTH", "subscription")
    monkeypatch.setenv("FAKE_WRAP", "fork")
    req = request(tmp_path, cwd=str(machine.root)).model_copy(update={"local_guard": machine.guard})
    with pytest.raises(RuntimeError, match="apiKeySource"):
        agent_cc.run(req)
    time.sleep(3)
    assert not marker.exists()


def test_a_stop_takes_the_forked_agent_down_even_when_ps_cannot_run(machine, service, tmp_path, monkeypatch):
    marker = tmp_path / "agent-ran-on"
    monkeypatch.setenv("FAKE_WRAP", "fork")
    process = launch(machine, "pi", cmd=[sys.executable, "-c",
                                         f"import time; time.sleep(2); open({str(marker)!r}, 'w')"])

    def no_fork(*args, **kwargs):
        raise BlockingIOError("process table full")
    monkeypatch.setattr(rules, "subprocess", type("S", (), {"run": staticmethod(no_fork)}))
    process.stop_tree()
    time.sleep(3)
    assert not marker.exists()


def test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child(machine, service, tmp_path, monkeypatch):
    bad = os.fsencode(tmp_path) + b"/\xff"
    with open(bad, "wb") as script:
        script.write(b"#!/bin/sh\n"
                     b"sh -c 'sleep 2; : > \"$1\"' grandchild \"$2\" &\n"
                     b"echo $$ > \"$1.pid\"\n"
                     b"sleep 2\n"
                     b": > \"$1\"\n")
    os.chmod(bad, 0o755)
    child, grandchild = tmp_path / "child", tmp_path / "grandchild"
    monkeypatch.setenv("FAKE_WRAP", "fork")
    process = launch(machine, "pi", cmd=[sys.executable, "-c",
                     f"import subprocess, time; subprocess.Popen([{bad!r}, {str(child)!r}, {str(grandchild)!r}]); "
                     "time.sleep(30)"])
    pidfile = tmp_path / "child.pid"
    deadline = time.time() + 10
    while time.time() < deadline and not (pidfile.exists() and pidfile.read_text().strip()):
        time.sleep(0.05)
    pid = int(pidfile.read_text())
    assert b"(\xff)" in Path(f"/proc/{pid}/stat").read_bytes()
    process.stop_tree()
    time.sleep(3)
    assert not child.exists() and not grandchild.exists()


# ── what the first real runs found (rollout step 2) ─────────────────────────

NET_YAML = ROOT_REPO / "docs" / "designs" / "agentsh" / "sssf-net.yaml"
FIRST_RUNS = ROOT_REPO / "docs" / "designs" / "agentsh" / "first-real-runs"


def real_argvs() -> list[list[str]]:
    return json.loads((REAL / "claude-code-git-argv.json").read_text())


@pytest.mark.parametrize("index", range(5))
def test_the_real_claude_code_git_argv_lists_are_not_push_denied(index):
    assert not push_denied(real_argvs()[index])


def test_the_fixture_keeps_the_hook_name_that_broke_the_baseline():
    assert any("hook.pre-push.enabled=false" in a for argv in real_argvs() for a in argv)


@pytest.mark.parametrize("argv", [
    ["git", "-c", "ALIAS.p=push", "p"], ["git", "-c", "alias.p=PUSH", "p"],
    ["git", "-c", "alias.p=!f(){ git push; }; f", "p"], ["git", "-c", "alias.p=!git;push", "p"],
    ["git", "-c", 'alias.p=!git "push"', "p"], ["git", "-c", "alias.p=!sh -c 'git push'", "p"]])
def test_any_alias_value_naming_push_is_denied(argv):
    assert push_denied(argv)


@pytest.mark.skipif(not NET_YAML.is_file(), reason="docs/ is not installed in this repo")
def test_the_push_patterns_equal_the_designs_network_policy():
    theirs = next(r for r in yaml.safe_load(NET_YAML.read_text())["command_rules"]
                  if r["name"] == "sssf-no-push")["args_patterns"]
    assert push_patterns() == theirs and len(theirs) == 3


# null events page

@pytest.mark.parametrize("body,expected", [(b"null", []), (b'{"events": null}', []), (b"[]", []),
                                           (b'{"events": [{"id": 1}, 3]}', [{"id": 1}])])
def test_get_reads_the_bodies_the_service_can_send(service, body, expected):
    service.raw_body = body
    assert bare(service)._get({"limit": 5}) == expected


@pytest.mark.parametrize("body", [b'"x"', b"42", b"true", b"1.5", b"{", b'{"events": "x"}', b'{"events": 3}'])
def test_get_raises_on_a_body_that_is_not_rows(service, body):
    service.raw_body = body
    with pytest.raises(Exception):
        bare(service)._get({"limit": 5})


def test_get_raises_on_an_http_error(service):
    service.status = 500
    with pytest.raises(Exception):
        bare(service)._get({"limit": 5})


def test_a_null_page_past_the_end_is_not_a_failed_poll(machine, service):
    service.null_past_end = True
    service.events = []
    process = launch(machine, "claude_code", cmd=[sys.executable, "-c", "import time; time.sleep(0.6)"])
    assert process.wait() == 0                       # the watcher never stopped the tree
    agentsh_guard.close(process)
    assert process.failure == "" and process._failed_polls == 0
    agentsh_guard.check_stopped(process, "b")


@pytest.mark.parametrize("body", [None, b'"x"', b"42"])
def test_three_bad_polls_in_a_row_still_say_the_service_stopped_answering(machine, service, body):
    if body is None:
        service.status = 500
    else:
        service.raw_body = body
    process = launch(machine, "pi")
    process.wait()
    assert "stopped answering event polls" in process.failure


# argv-only exec denial

def argv_event(n: int) -> dict:
    event = json.loads((REAL / "execve-denial-event.json").read_text())
    event["id"] = f"argv-{n}"
    assert "command" not in event and "command" not in event["fields"]
    return event


def test_an_exec_denial_with_argv_only_names_its_command(service):
    process = bare(service)
    process._take([argv_event(i) for i in range(3)])
    assert process.counted == 3
    name, command = process.last
    assert name and command.startswith("git") and command.endswith("config user.name")
    assert len(command) <= agentsh_guard.COMMAND_CHARS + len(" … ")
    stop = agentsh_guard.breach(process, "b")
    assert stop is not None
    text = str(stop)
    assert "config user.name" in text and "last `` `" not in text and "last ``" not in text


def test_a_file_denial_without_a_path_falls_back_to_the_argv_command(service):
    process = bare(service)
    event = argv_event(1)
    event["type"] = "ptrace_file"
    process._take([event])
    assert process.other_denials[0]["path"].endswith("config user.name")


def test_command_text():
    command_text = agentsh_guard.command_text
    assert command_text({"fields": {"argv": ["/usr/bin/git", "status", "-s"]}}) == "git status -s"
    assert command_text({"command": "gh pr 1", "fields": {"argv": ["/usr/bin/git", "x"]}}) == "gh pr 1"
    assert command_text({"fields": {"command": "gh pr 2"}}) == "gh pr 2"
    assert command_text({"fields": {"argv": ["echo", "a\nb\tc\rd"]}}) == "echo a\\nb\\tc\\rd"
    assert command_text({}) == "" and command_text({"fields": {"argv": []}}) == ""
    assert command_text({"fields": {"argv": ["git", 3]}}) == ""
    assert command_text({"command": "", "fields": {"argv": ["git", "log"]}}) == "git log"


# file rules, judged by a Python emulation of the engine's glob (gobwas, separator `/`)

def glob_regex(glob: str) -> "re.Pattern":
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "\\" and i + 1 < len(glob):
            i += 1
            out.append(re.escape(glob[i]))
        elif c == "[":
            j = glob.index("]", i)
            body = glob[i + 1:j]
            out.append("[^" + body[1:] + "]" if body.startswith("!") else "[" + body + "]")
            i = j
        elif c == "{":
            j = glob.index("}", i)
            out.append("(?:" + "|".join(re.escape(a) for a in glob[i + 1:j].split(",")) + ")")
            i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z", re.S)


class Rules:
    def __init__(self, text: str, home: str, root: str):
        self.rules = yaml.safe_load(text)["file_rules"]
        self.home, self.root = home, root

    def _globs(self, rule_):
        return [glob_regex(p.replace("${HOME}", self.home).replace("${PROJECT_ROOT}", self.root))
                for p in rule_["paths"]]

    def first_match(self, path: str, op: str) -> str:
        for rule_ in self.rules:
            if ("*" in rule_["operations"] or op in rule_["operations"]) \
                    and any(g.match(path) for g in self._globs(rule_)):
                return rule_["name"]
        return "" if op in agentsh_guard.READ else "default-deny-files"

    def decision(self, name: str) -> str:
        return next((r["decision"] for r in self.rules if r["name"] == name), "deny")

    def link(self, link_path: str, target: str) -> str:
        """Build 05b3777f judges the link's own path as `symlink` and the raw target as
        `symlink_target`; the first deny wins."""
        for each, op in ((link_path, "symlink"), (target, "symlink_target")):
            name = self.first_match(each, op)
            if self.decision(name) == "deny":
                return name
        return "allow"


HOME = "/home/u"
WORKSPACE = "/w/wt"      # a `--worktree` run's root, apart from the main checkout


def both_harnesses():
    config = agents.load_config(str(SHIPPED))
    other = config.agents[0].model_copy(deep=True)
    other.coding_agent = "claude_code"
    config.agents.append(other)
    return config


@pytest.fixture(params=["claude_code", "pi"])
def world(request, tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "proj").resolve()
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text(
        "local_guard: agentsh\nsecret_files: ['*.pem', './cfg/*.key']\n")
    monkeypatch.chdir(repo)
    config = both_harnesses()
    guard = agentsh_guard.read(config)
    text = guard.policies[request.param].text
    sessions = str(repo / "adws/adw_data/sessions")
    return type("World", (), {
        "harness": request.param, "claude": request.param == "claude_code", "text": text,
        "R": WORKSPACE, "MAIN": str(repo), "S": sessions, "G": str(repo / ".git"), "H": HOME,
        "refuse": str(Path(agentsh_guard.__file__).resolve().with_name("sssf_refuse.py")),
        "rules": Rules(text, HOME, WORKSPACE)})


def test_the_sessions_folder_is_open_and_the_denies_still_hold_inside_it(world):
    r = world.rules
    for op in ("write", "create", "read"):
        assert r.first_match(f"{world.S}/x/context_handoff/plan.md", op) == "sssf-sessions"
    assert r.first_match(f"{world.S}/x/.env", "read") == "sssf-secret-files"
    assert r.first_match(f"{world.S}/x/.env.local", "write") == "sssf-secret-files"
    assert r.first_match(f"{world.S}/x/a.pem", "read") == "sssf-repo-secret-files"
    assert r.first_match(f"{HOME}/.ssh/id_rsa", "read") == "sssf-credentials"
    assert r.first_match("/proc/1/environ", "read") == "sssf-proc-secrets"
    for path in (f"{world.G}/hooks/pre-commit", f"{world.G}/config"):
        assert r.first_match(path, "write") == "sssf-git-config"
    names_ = [x["name"] for x in r.rules]
    at = names_.index("sssf-sessions")
    assert all(x["decision"] == "allow" for x in r.rules[at:-1])
    assert names_[-1] == "sssf-default-deny"


def test_the_refuse_script_is_readable_by_claude_only(world):
    r = world.rules
    if world.claude:
        assert r.first_match(world.refuse, "read") == "sssf-refuse-script"
        assert r.first_match(world.refuse, "write") != "sssf-refuse-script"
        assert world.refuse.endswith("/adw_modules/sssf_refuse.py") and f"- '{world.refuse}'" in world.text
    else:
        assert "sssf-refuse-script" not in [x["name"] for x in r.rules]


@pytest.mark.parametrize("target", ["testing", "./x", "dir/file", "../x", ".env"])
def test_a_relative_symlink_in_the_workspace_is_denied(world, target):
    # No shipped deny glob matches a bare target: `**/.env` and `**/*.pem` need a `/` before the name.
    for link_path in (f"{world.R}/l", f"{world.R}/sub/l", f"{world.S}/x/l", "/tmp/l"):
        assert world.rules.link(link_path, target) == "default-deny-files"


def test_relative_symlinks_elsewhere_are_denied(world):
    r = world.rules
    for path, target in [
            (f"{world.G}/x", "config"), (f"{world.G}/worktrees/id/x", "y"),
            (f"{HOME}/.claude/x", "settings.json"), (f"{HOME}/.claude/projects/x", "y"),
            (f"{HOME}/.claude.json.x", ".ssh/id_rsa"), (f"{HOME}/.pi", ".ssh"),
            (f"{HOME}/.cache/uv", "x"), (world.S, "worktrees"),
            ("/run/user/1000/cc-socks", "gnupg/S.gpg-agent")]:
        assert r.decision(r.link(path, target)) == "deny", (path, target)


def test_no_file_rule_allows_a_relative_path(world):
    for rule_ in world.rules.rules:
        if rule_["decision"] == "allow" and rule_["name"] != "sssf-env-examples":
            assert all(p.startswith(("/", "${")) for p in rule_["paths"]), rule_["name"]


def test_absolute_links_work_as_on_main(world):
    r = world.rules
    assert r.link(f"{world.R}/.venv/bin/python",
                  f"{world.MAIN}/adws/adw_data/agent-cache/uv-python/cpython/bin/python3.12") == "allow"
    assert r.link(f"{world.R}/.venv/bin/python",
                  f"{HOME}/.local/share/uv/python/cpython/bin/python3.12") == "allow"
    for target in (f"{world.G}/config", f"{world.G}/hooks/pre-push",
                   f"{world.MAIN}/.git/config", f"{world.MAIN}/.git/hooks/pre-push"):
        assert r.link(f"{world.R}/l", target) == "sssf-git-config", target
    for target in (f"{HOME}/.claude/settings.json", f"{HOME}/.claude/skills/x/SKILL.md"):
        if world.claude:
            assert r.link(f"{world.R}/l", target) == "sssf-agent-config", target
        else:
            assert r.decision(r.link(f"{world.R}/l", target)) == "deny", target
    assert r.link(f"{world.R}/l", f"{world.R}/src/a") == "allow"
    assert r.link(f"{world.R}/l", f"{world.R}/package.json") == "allow"
    assert r.link(f"{world.S}/x/l", f"{world.S}/x/plan.md") == "allow"
    assert r.link(f"{world.R}/l", "/etc/passwd") == "sssf-default-deny"
    assert r.link(f"{world.R}/l", f"{HOME}/.ssh/id_rsa") == "sssf-credentials"
    assert r.link(f"{world.R}/l", f"{world.R}/.env") == "sssf-secret-files"
    assert r.link(f"{world.R}/l", f"{world.R}/cfg/a.key") == "sssf-repo-secret-files"
    if world.claude:
        assert r.link(f"{HOME}/.claude/skills/x", f"{world.R}/y") == "sssf-agent-config"


MAIN_FILE_RULES = [   # b307ef3's agentsh_guard.py
    "sssf-repo-secret-files", "sssf-env-examples", "sssf-secret-files", "sssf-credentials",
    "sssf-proc-secrets", "sssf-other-sign-in", "sssf-git-config",
    "sssf-workspace", "sssf-git-data", "sssf-main-config", "sssf-temp", "sssf-devices",
    "sssf-fd-leaves", "sssf-mkdir-parents", "sssf-tool-write", "sssf-agent-write",
    "sssf-config-search", "sssf-system-read", "sssf-home-read",
    "sssf-default-deny"]   # plus sssf-home-folder, added with build 05b3777f


def test_the_file_rule_order_matches_main_around_the_new_allows(world):
    order = [x["name"] for x in world.rules.rules]
    assert order.index("sssf-workspace") == order.index("sssf-git-config") + 1
    assert order.index("sssf-sessions") == order.index("sssf-git-data") + 1
    assert order[-3:] == ["sssf-home-folder", "sssf-home-read", "sssf-default-deny"]
    assert order.index("sssf-home-folder") == order.index("sssf-system-link") + 1
    assert order.index("sssf-agent-cache") == order.index("sssf-sessions") + 1
    assert order.index("sssf-config-folders") == order.index("sssf-config-search") + 1
    assert order.index("sssf-system-link") == order.index("sssf-system-read") + 1
    assert "sssf-tool-write" not in order
    wanted = (set(MAIN_FILE_RULES) - {"sssf-tool-write"}) | {
        "sssf-sessions", "sssf-agent-cache", "sssf-config-folders", "sssf-system-link",
        "sssf-home-folder"} | (
        {"sssf-refuse-script", "sssf-agent-config", "sssf-agent-read"} if world.claude else set())
    assert set(order) == wanted and len(order) == len(wanted)



# paths

def test_read_puts_the_sessions_folder_in_the_main_checkout(tmp_path, monkeypatch):
    cfg = both_harnesses()
    repo = git_repo(tmp_path / "proj").resolve()
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: agentsh\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e.com",
                    "commit", "-qm", "init"], check=True)
    monkeypatch.chdir(repo)
    wanted = str(repo / "adws/adw_data/sessions")
    before = agentsh_guard.read(cfg)
    assert all(f"- '{wanted}'" in p.text and f"- '{wanted}/**'" in p.text for p in before.policies.values())
    from adw_modules import worktree
    worktree.anchor(cfg, repo)
    after = agentsh_guard.read(cfg)
    assert {h: p.text for h, p in before.policies.items()} == {h: p.text for h, p in after.policies.items()}
    link = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(link), "-b", "w"], check=True)
    monkeypatch.chdir(link)
    inside = agentsh_guard.read(both_harnesses())
    assert {h: p.text for h, p in before.policies.items()} == {h: p.text for h, p in inside.policies.items()}
    claude = before.policies["claude_code"].text
    assert str(Path(agentsh_guard.__file__).resolve().with_name("sssf_refuse.py")) in claude


@pytest.mark.skipif(not FIRST_RUNS.is_dir(), reason="docs/ is not installed in this repo")
@pytest.mark.parametrize("name", ["claude-code-git-argv.json", "events-page-past-end.json",
                                  "execve-denial-event.json"])
def test_the_fixture_copies_equal_their_sources(name):
    assert (REAL / name).read_bytes() == (FIRST_RUNS / name).read_bytes()


# ── bypass review 1 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["ALIAS", "Alias", "aliaS"])
def test_an_alias_key_in_another_case_is_still_denied(key):
    assert push_denied(["git", "-c", f"{key}.p=push", "p"])


# ── tool config above the workspace, agent caches, system links ──────────────

MAINS = ["/home/u/github/super-simple-software-factory", "/home/u/github/repo"]
CONFIG_FILES = ("uv.toml", "pyproject.toml", ".python-version", ".python-versions",
                "package.json", "pnpm-workspace.yaml")


def shipped_text(main: str, harness: str) -> str:
    return agentsh_guard.policy_text(repo_settings.RepoSettings(), f"{main}/.git", harness, "n",
                                     sessions=f"{main}/adws/adw_data/sessions",
                                     refuse_script=f"{main}/adws/adw_modules/sssf_refuse.py")


def rule_of(text: str, name: str) -> dict:
    return next(r for r in yaml.safe_load(text)["file_rules"] if r["name"] == name)


def folders_of(main: str) -> list[str]:
    return ["/", "/home", "/home/u", "/home/u/github", main, f"{main}/adws",
            f"{main}/adws/adw_data", f"{main}/adws/adw_data/worktrees"]


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
@pytest.mark.parametrize("main", MAINS)
def test_the_rendered_config_lists(main, harness):
    text = shipped_text(main, harness)
    folders = rule_of(text, "sssf-config-folders")
    assert folders["paths"] == folders_of(main) and folders["operations"] == ["open", "stat", "readlink", "access"]
    search = rule_of(text, "sssf-config-search")
    want = [f"{d.rstrip('/')}/{n}" for d in folders_of(main) for n in CONFIG_FILES] + ["${HOME}/.bunfig.toml"]
    assert len(want) == 49 and search["paths"] == want and search["operations"] == agentsh_guard.READ


def test_folders_above_with_a_data_root_outside_main():
    got = agentsh_guard.folders_above("/home/u/p", "/data/x")
    assert got == sorted(got, key=lambda d: (len(Path(d).parts), d))
    assert set(got) == {"/", "/home", "/home/u", "/data", "/data/x", "/data/x/worktrees"}
    assert len(got) == len(set(got))


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
@pytest.mark.parametrize("main", MAINS)
@pytest.mark.parametrize("worktree", [False, True])
def test_config_lookups_pass_and_the_rest_is_denied(main, harness, worktree):
    root = f"{main}/adws/adw_data/worktrees/abc" if worktree else main
    r = Rules(shipped_text(main, harness), HOME, root)
    above = [d for d in folders_of(main) if root.startswith(d.rstrip("/") + "/")]
    assert above
    for folder in above:
        for name in CONFIG_FILES:
            for op in agentsh_guard.READ:
                assert r.first_match(f"{folder.rstrip('/')}/{name}", op) == "sssf-config-search"
        for op in agentsh_guard.FOLDER_OPS:
            assert r.decision(r.first_match(folder, op)) == "allow"
    assert r.first_match(f"{HOME}/.bunfig.toml", "read") == "sssf-config-search"
    for op in ("list", "read"):
        assert r.decision(r.first_match(f"{HOME}/github", op)) == "deny"
    if worktree:
        for folder in (f"{main}/adws", f"{main}/adws/adw_data", f"{main}/adws/adw_data/worktrees"):
            for op in ("list", "read"):
                assert r.decision(r.first_match(folder, op)) == "deny", (folder, op)
    for path in (f"{HOME}/github/sibling/pyproject.toml", f"{HOME}/github/sibling",
                 f"{HOME}/github/notes.txt", f"{HOME}/github/uv.toml.bak", f"{HOME}/github/uv.toml/x",
                 f"{main}/adws/adw_data/worktrees/other/pyproject.toml",
                 f"{HOME}/github/other/adws/adw_data/agent-cache/x"):
        if path.startswith(root + "/"):
            continue
        assert r.decision(r.first_match(path, "read")) == "deny", path
    if worktree:
        assert r.decision(r.first_match(f"{main}/README.md", "write")) == "deny"
    listed = [f"{d.rstrip('/')}/{n}" for d in above for n in CONFIG_FILES] + above
    for path in listed:
        for op in ("write", "create", "delete", "symlink", "symlink_target"):
            assert r.decision(r.first_match(path, op)) == "deny", (path, op)


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_agent_caches_are_open_and_the_engineers_are_closed(harness):
    main = MAINS[0]
    r = Rules(shipped_text(main, harness), HOME, f"{main}/adws/adw_data/worktrees/abc")
    cache = f"{main}/adws/adw_data/agent-cache"
    for path in (f"{cache}/uv/x", f"{cache}/cargo/registry/x"):
        assert r.first_match(path, "write") == "sssf-agent-cache"
    assert r.first_match(f"{cache}/x/" + ".env", "read") == "sssf-secret-files"
    for path in ("~/.cache/uv/x", "~/.cache/uv", "~/.local/share/uv/python/x", "~/.local/share/uv/tools/x",
                 "~/.local/share/uv/x", "~/.npm/x", "~/.npm", "~/.cargo/registry/x", "~/.cargo/git/x",
                 "~/.local/bin/x", "~/.bun/install/cache/x", "~/.cache/node/corepack/x",
                 f"{HOME}/github/other/adws/adw_data/agent-cache/x"):
        path = path.replace("~", HOME)
        for op in ("write", "create", "mkdir"):
            assert r.decision(r.first_match(path, op)) == "deny", (path, op)
    install = f"{HOME}/.local/share/uv/python/cpython-3.13.10-linux-x86_64-gnu"
    for path in (f"{install}/bin/python3.13", f"{install}/lib/python3.13/site-packages/x.pth",
                 f"{HOME}/.local/share/uv/python/.lock", f"{HOME}/.npmrc"):
        for op in ("write", "create", "mkdir", "rename", "delete", "link", "chmod"):
            assert r.decision(r.first_match(path, op)) == "deny", (path, op)
    for path in ("~/.local/share/uv/python/cpython-3.12/bin/python3.12", "~/.local/share/uv/tools/ruff/bin/ruff"):
        assert r.decision(r.first_match(path.replace("~", HOME), "read")) == "allow"
    for path in ("~/.cache/uv/x", "~/.npm/x", "~/.local/share/uv/credentials/credentials.toml"):
        assert r.decision(r.first_match(path.replace("~", HOME), "read")) == "deny"


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_links_to_system_pythons(harness):
    main = MAINS[0]
    r = Rules(shipped_text(main, harness), HOME, main)
    for target in ("/usr/bin/python3.12", "/bin/python3"):
        assert r.link(f"{main}/.v/bin/python", target) == "allow"
    for path in ("/usr/lib/x", "/usr/local/bin/x", "/opt/x", "/opt/a/x", "/mnt/c/x", "/mnt/x", "/etc/x",
                 "/x", "/home/x", "/var/x"):
        assert r.decision(r.link(path, "/usr/bin/python3.12")) == "deny", path
    for target in ("/usr/lib/x86_64-linux-gnu/libc.so.6", "/usr/local/bin/python3", "/opt/py/bin/python3",
                   "/mnt/c/x", "/etc/passwd", "/proc/self/cwd/x"):
        assert r.decision(r.link(f"{main}/l", target)) == "deny", target
    assert agentsh_guard.SYSTEM_LINK == [
        "/usr/bin/**", "/bin/**", "${HOME}/.local/share/uv/python/*/bin/python3.*"]
    assert rule_of(shipped_text(main, harness), "sssf-system-link")["operations"] == ["symlink_target"]
    # A link to a managed interpreter works: build 05b3777f judges a link's target apart from its
    # own path, and no rule allows a link's own path in the engineer's uv tree.
    install = f"{HOME}/.local/share/uv/python/cpython-3.13.10-linux-x86_64-gnu"
    assert r.link(f"{main}/.v/bin/python", f"{install}/bin/python3.13") == "allow"
    for target in (f"{install}/lib/python3.13/os.py", f"{install}/bin/pip",
                   f"{HOME}/.local/share/uv/tools/ruff/bin/ruff", f"{HOME}/.local/share/uv/python/x"):
        assert r.decision(r.link(f"{main}/.v/bin/python", target)) == "deny", target
    for path in (f"{install}/bin/python3.99", f"{install}/lib/python3.13/site-packages/sitecustomize.py",
                 f"{HOME}/.local/share/uv/python/x", f"{HOME}/.local/share/uv/x"):
        for target in ("/usr/bin/python3.12", f"{main}/evil.sh", "/tmp/evil.sh"):
            assert r.decision(r.link(path, target)) == "deny", (path, target)
    for glob in agentsh_guard.SYSTEM_LINK:
        assert r.first_match(glob.replace("${HOME}", HOME), "symlink") != "sssf-system-link", glob


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_links_judge_the_target_apart_from_the_path(harness):
    text = shipped_text(MAINS[0], harness)
    assert "symlink_target" in rule_of(text, "sssf-git-config")["operations"]
    if harness == "claude_code":
        assert "symlink_target" in rule_of(text, "sssf-agent-config")["operations"]
    for rule_ in yaml.safe_load(text)["file_rules"]:
        if rule_["decision"] == "allow" and rule_["operations"] != ["*"] \
                and "symlink_target" in rule_["operations"]:
            assert rule_["name"] == "sssf-system-link", rule_["name"]


@pytest.mark.parametrize("harness", ["claude_code", "pi"])
@pytest.mark.parametrize("main", MAINS + ["/srv/repo"])
def test_the_home_folder_is_opened_not_listed(harness, main):
    text = shipped_text(main, harness)
    r = Rules(text, HOME, main)
    folder = rule_of(text, "sssf-home-folder")
    assert folder == {"name": "sssf-home-folder", "paths": ["${HOME}"],
                      "operations": agentsh_guard.FOLDER_OPS, "decision": "allow"}
    assert "${HOME}" not in rule_of(text, "sssf-home-read")["paths"]
    assert r.decision(r.first_match(HOME, "list")) == "deny"
    for op in agentsh_guard.FOLDER_OPS:
        assert r.decision(r.first_match(HOME, op)) == "allow", op
    for path in ("/", "/home", f"{HOME}/.config", f"{HOME}/.local/share/uv/python", main):
        assert r.decision(r.first_match(path, "list")) == "allow", path
    for path in (f"{HOME}/github", f"{HOME}/github/sibling"):
        assert r.decision(r.first_match(path, "list")) == "deny", path
    assert r.decision(r.first_match(f"{HOME}/github/sibling/README.md", "read")) == "deny"
    if harness == "claude_code":
        assert r.decision(r.first_match(f"{HOME}/.claude", "list")) == "allow"


def test_read_sets_the_agent_cache_in_the_main_checkout(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "proj").resolve()
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: agentsh\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e.com",
                    "commit", "-qm", "init"], check=True)
    monkeypatch.chdir(repo)
    wanted = str(repo / "adws/adw_data/agent-cache")
    assert agentsh_guard.read(both_harnesses()).agent_cache == wanted
    link = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(link), "-b", "w"], check=True)
    monkeypatch.chdir(link)
    assert agentsh_guard.read(both_harnesses()).agent_cache == wanted


def test_glob_characters_in_the_checkout_path_match_themselves():
    main = "/home/u/github/repo[old]"
    # A worktree run: the workspace is apart from main, whose ${PROJECT_ROOT} the emulation leaves unescaped.
    for harness in ("claude_code", "pi"):
        text = shipped_text(main, harness)
        for root in (f"{main}/adws/adw_data/worktrees/abc",):
            r = Rules(text, HOME, root)
            sibling = "/home/u/github/repoo"
            for path in (f"{sibling}/adws/adw_data/agent-cache/uv/x", f"{sibling}/adws/pyproject.toml",
                         f"{sibling}/adws/adw_data/worktrees", f"{sibling}/.git/refs/heads/main",
                         f"{sibling}/adws/adw_data/sessions/x"):
                for op in ("write", "create", "delete", "symlink", "read", "open"):
                    assert r.decision(r.first_match(path, op)) == "deny", (path, op)
    r = Rules(text, HOME, f"{main}/adws/adw_data/worktrees/abc")
    assert r.first_match(f"{main}/adws/adw_data/agent-cache/uv/x", "write") == "sssf-agent-cache"
    assert r.first_match(f"{main}/adws/pyproject.toml", "read") == "sssf-config-search"
    assert agentsh_guard.glob_escape("/a[b]{c}*?\\d") == "/a\\[b\\]\\{c\\}\\*\\?\\\\d"
