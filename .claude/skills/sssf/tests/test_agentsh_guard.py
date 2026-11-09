"""local_guard: agentsh (agentsh_guard.py), against a fake `agentsh` script and a fake HTTP service.

The config comes from the sssf.config.yaml SSSF ships, never a test-only one. A test
that needs the real service skips unless AGENTSH_LIVE is set.
"""

from __future__ import annotations

import hashlib
import json
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
TEMPLATE = SKILL / "templates" / "adws" / "adw_modules" / "agentsh_sssf.yaml"
DENY_POLICY = ROOT_REPO / "docs" / "designs" / "agentsh" / "deny-policy.py"


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
                          text=agentsh_guard.policy_text(settings, common, h, n))
                for h in ("claude_code", "pi")}
    template = TEMPLATE.read_text()
    guard = LocalGuard(mode="agentsh", root=str(root.resolve()), policies=policies, config_template=template)
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
    refused("not the file SSSF renders")


def test_stale_and_missing_policies_and_a_wrong_cwd_refuse(machine, monkeypatch):
    name = machine.guard.policies["pi"].name
    (machine.pdir / f"{name}.yaml").write_text("old")
    with pytest.raises(agentsh_guard.AgentshRefused, match="stale"):
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


# ── generated policies ───────────────────────────────────────────────────────

def names(text: str) -> list[str]:
    return [line.split(": ", 1)[1].strip("'") for line in text.splitlines() if line.startswith("  - name: ")]


def test_rule_order_in_the_generated_policy():
    settings = repo_settings.RepoSettings(
        secret_files=["*.pem", "./cfg/*.key"],
        deny_exec=[{"name": "no-rm", "command": "rm", "args": "-rf", "reason": "no deletes"}],
        network_allow=["a.example.com"])
    text = agentsh_guard.policy_text(settings, "/m/proj/.git", "claude_code", "sssf-proj-claude")
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
    text = agentsh_guard.policy_text(settings, "/m/o'brien/.git", "pi", "sssf-x-pi")
    parsed = yaml.safe_load(text)
    paths = [p for r in parsed["file_rules"] for p in r["paths"]]
    assert "${PROJECT_ROOT}/it's/a.key" in paths and "/m/o'brien/.git" in paths


@pytest.mark.skipif(not DENY_POLICY.is_file(), reason="docs/ is not installed in this repo")
@pytest.mark.parametrize("harness,short", [("claude_code", "claude"), ("pi", "pi")])
def test_file_rules_equal_the_designs_deny_policy_output(tmp_path, harness, short):
    subprocess.run([sys.executable, str(DENY_POLICY), "/m/proj/.git", str(tmp_path)],
                   check=True, capture_output=True)
    theirs = (tmp_path / f"sssf-dd-{short}.yaml").read_text()

    def section(text):
        return text[text.index("file_rules:"):text.index("network_rules:")]
    ours = agentsh_guard.policy_text(repo_settings.RepoSettings(), "/m/proj/.git", harness, "n")
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

FIXTURES = SKILL / "tests" / "fixtures"


def push_patterns(harness="pi") -> list[str]:
    text = agentsh_guard.policy_text(repo_settings.RepoSettings(), "/r/.git", harness, "p")
    rule_ = next(r for r in yaml.safe_load(text)["command_rules"] if r["name"] == "sssf-no-push")
    return rule_["args_patterns"]


def push_denied(argv: list[str]) -> bool:
    joined = " ".join(argv)
    return any(re.search(p, joined, re.ASCII) for p in push_patterns())


@pytest.mark.parametrize("argv", [
    ["git", "commit", "-m", "fix the push path"], ["git", "commit", "-m", "push"],
    ["git", "stash", "push"], ["git", "log", "--grep", "push"]])
def test_push_cases_that_run(argv):
    assert not push_denied(argv)


@pytest.mark.parametrize("argv", [
    ["git", "push"], ["git", "-c", "a=b", "push"], ["git", "-c", "a=b c", "push"],
    ["git", "-C", "/x", "push"], ["git", "--git-dir", "/x", "push"],
    ["git", "-c", "alias.p=push", "p"], ["git", "-c", "a=b", "commit", "-m", "push"], ["", "push"]])
def test_push_cases_that_are_denied(argv):
    assert push_denied(argv)


def test_install_script_writes_nothing_under_the_repo_and_agrees_with_the_check(
        tmp_path, monkeypatch, machine, capsys):
    sys.path.insert(0, str(SKILL / "templates" / "adws"))
    import adw_agentsh_policy as command
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = git_repo(tmp_path / "throwaway")
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text("local_guard: agentsh\n")
    (repo / "adws/adw_data").symlink_to(outside)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e.com",
                    "commit", "-qm", "init"], check=True)
    monkeypatch.chdir(repo)

    def status():
        return subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True,
                              text=True, check=True).stdout

    def install_and_check():
        before = status()
        assert command.main(str(SHIPPED)) == 0
        assert status() == before and list(outside.iterdir()) == []
        steps = [shlex.split(line) for line in capsys.readouterr().out.splitlines()
                 if line.startswith("sudo install")]
        assert steps
        for step in steps:
            source = Path(step[-2])
            assert source.exists() and not source.resolve().is_relative_to(repo.resolve())
            shutil.copy(source, step[-1])
        guard = agentsh_guard.read(agents.load_config(str(SHIPPED)))
        agentsh_guard.check_machine(guard)
        return guard

    machine.config.unlink()
    install_and_check()
    # Another repo's names are already in the installed list.
    machine.config.write_text(agentsh_guard.render_config(
        machine.guard.config_template, [p.name for p in machine.guard.policies.values()]))
    guard = install_and_check()
    allowed = yaml.safe_load(machine.config.read_text())["policies"]["allowed"]
    assert set(allowed) >= {p.name for p in machine.guard.policies.values()} | {p.name for p in guard.policies.values()}


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
    with pytest.raises(agentsh_guard.AgentshRefused, match="not the file SSSF renders"):
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
    with pytest.raises(agentsh_guard.AgentshRefused, match="stale"):
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
    text = agentsh_guard.policy_text(settings, "/r/.git", "pi", "p")
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
