"""The starter harness's pi subagents (templates/harness_engineering/subagents.ts) under SSSF.

The shipped subagents.ts runs unmodified in node (driver.mjs, with stubs for the pi packages).
Its real spawn("pi", ...) starts a stand-in pi (fixtures/subagents/pi) that loads the real
sssf_rules.ts from its argv. No real pi runs and nothing is written under ~/.pi. Rules come from
the repo.yaml SSSF ships, and the stream check is SSSF's own (agents._event_forwarder).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, rules  # noqa: E402
from adw_modules.repo_settings import AgentRule  # noqa: E402

FIXTURES = SKILL / "tests" / "fixtures" / "subagents"
SUBAGENTS = SKILL / "templates" / "harness_engineering" / "subagents.ts"
REFUSE_EXT = SKILL / "templates" / "adws" / "adw_modules" / "sssf_rules.ts"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def shipped_push_rule(on_match):
    """The push rule from the starter's commented agent_rules in templates/repo.yaml, as shipped."""
    lines = (SKILL / "templates" / "repo.yaml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("#     - rule: Never run `git push`"))
    body = "\n".join(line[2:] for line in lines[start:start + 3])
    return AgentRule(**{**yaml.safe_load(body)[0], "on_match": on_match})


class Run:
    """One driver run in its own temp tree, with a stand-in pi first on PATH."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        shutil.copy(FIXTURES / "pi", self.bin / "pi")
        (self.bin / "pi").chmod(0o755)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.session_dir = tmp_path / "sessions"
        self.session_dir.mkdir()
        self.log = tmp_path / "pi-log.json"
        self.marker = tmp_path / "marker"
        self.pidfile = tmp_path / "pid"
        self.result = tmp_path / "result.json"

    def env(self, sssf=True, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("SSSF_", "PI_CODING", "FAKE_PI", "DRIVER_"))}
        env.update(PATH=f"{self.bin}{os.pathsep}{env['PATH']}", HOME=str(self.home),
                   FAKE_PI_LOG=str(self.log), FAKE_PI_MARKER=str(self.marker),
                   FAKE_PI_PIDFILE=str(self.pidfile), DRIVER_RESULT=str(self.result),
                   DRIVER_MARKER=str(self.marker), DRIVER_TS=str(SUBAGENTS))
        if sssf:
            env.update(SSSF_REFUSE_EXTENSION=str(REFUSE_EXT), SSSF_SESSION_DIR=str(self.session_dir))
        env.update({k: v for k, v in extra.items() if v is not None})
        for k in [k for k, v in extra.items() if v is None]:
            env.pop(k, None)
        return env

    def start(self, task="look around", tool="subagent_create", args=None, **env):
        args = args if args is not None else {"task": task, "thinking": "low"}
        full = self.env(DRIVER_TOOL=tool, DRIVER_ARGS=json.dumps(args), **env)
        return subprocess.Popen(
            ["node", "--import", str(FIXTURES / "register.mjs"), str(FIXTURES / "driver.mjs")],
            env=full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def go(self, timeout=30, **kw):
        proc = self.start(**kw)
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err

    def go_plain(self, **env):
        """A run with no SSSF variable, as an interactive pi has it."""
        full = self.env(sssf=False, DRIVER_TOOL="subagent_create",
                        DRIVER_ARGS=json.dumps({"task": "look around", "thinking": "low"}), **env)
        return subprocess.run(
            ["node", "--import", str(FIXTURES / "register.mjs"), str(FIXTURES / "driver.mjs")],
            env=full, capture_output=True, text=True, timeout=30)

    def argv(self):
        return json.loads(self.log.read_text())

    def driver_result(self):
        return json.loads(self.result.read_text())


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path)


def result_text(r):
    return r.driver_result()["result"]["content"][0]["text"]


def forwarded(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


# ── the extension and the argv ───────────────────────────────────────────────

def test_the_child_loads_the_refuse_extension_as_its_only_extension(run):
    code, _, err = run.go()
    assert code == 0, err
    argv = run.argv()["argv"]
    assert argv.count("-e") == 1
    assert argv[argv.index("-e") + 1] == str(REFUSE_EXT)
    assert argv.index("--no-extensions") < argv.index("-e")
    assert argv.index("--no-prompt-templates") < argv.index("-e")
    assert argv.index("--no-themes") < argv.index("-e")
    assert argv[argv.index("-e"):] == ["-e", str(REFUSE_EXT), "--", "look around"]


def test_the_childs_environment_has_jitis_file_cache_off(run):
    code, _, err = run.go()
    assert code == 0, err
    assert run.argv()["env"]["JITI_FS_CACHE"] == "false"


def test_the_child_inherits_the_compile_cache_setting_under_sssf(run):
    code, _, err = run.go(NODE_DISABLE_COMPILE_CACHE="1")     # agent_pi.run puts it on the parent
    assert code == 0, err
    assert run.argv()["env"]["NODE_DISABLE_COMPILE_CACHE"] == "1"


def test_an_interactive_pis_child_gets_it_only_from_the_shell(run):
    done = run.go_plain(NODE_DISABLE_COMPILE_CACHE=None)
    assert done.returncode == 0, done.stderr
    assert "NODE_DISABLE_COMPILE_CACHE" not in run.argv()["env"]


def test_a_parent_with_the_cache_on_still_gives_the_child_it_off(run):
    code, _, err = run.go(JITI_FS_CACHE="true")
    assert code == 0, err
    assert run.argv()["env"]["JITI_FS_CACHE"] == "false"


def test_without_sssf_the_childs_cache_is_off_too(run):
    done = run.go_plain(JITI_FS_CACHE="true")
    assert done.returncode == 0, done.stderr
    assert run.argv()["env"]["JITI_FS_CACHE"] == "false"


@pytest.mark.parametrize("task", ["/x", "/skill:demo"])
def test_a_task_starting_with_a_slash_still_starts_a_child(run, task):
    proc = run.start(task=task)
    _, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    argv = run.argv()["argv"]
    assert argv[-2:] == ["--", task]
    assert "--no-prompt-templates" in argv


def test_a_childs_read_of_a_secret_file_and_a_matching_command_are_blocked(run):
    env_file = run.tmp / "secret.key"
    env_file.write_text("KEY=1\n")
    rule = shipped_push_rule("refuse")
    calls = [{"id": "r1", "toolName": "read", "input": {"path": str(env_file)}},
             {"id": "b1", "toolName": "bash", "input": {"command": "git push origin main"}},
             {"id": "ok", "toolName": "bash", "input": {"command": "ls"}}]
    code, out, err = run.go(FAKE_PI_CALLS=json.dumps(calls),
                            SSSF_SECRET_FILES=json.dumps([str(env_file)]),
                            SSSF_REFUSE_RULES=json.dumps(rules.refusing([rule])))
    assert code == 0, err
    ends = {e["toolCallId"]: e for e in forwarded(out) if e["type"] == "tool_execution_end"}
    assert ends["sub1:r1"]["isError"] and "durationMs" not in ends["sub1:r1"]
    assert "secret files" in ends["sub1:r1"]["result"]["content"][0]["text"]
    assert ends["sub1:b1"]["isError"] and "durationMs" not in ends["sub1:b1"]
    assert "durationMs" in ends["sub1:ok"]


# ── the forwarded events, through SSSF's own stream check ────────────────────

def stream_check(rule, can_refuse):
    events, said = [], []
    r = SimpleNamespace(adw_id="r1", tracer=SimpleNamespace(event=events.append),
                        console=SimpleNamespace(advise=said.append))
    interface = SimpleNamespace(ToolCallTracker=lambda: SimpleNamespace(observe=lambda e: []),
                                HARNESS="pi")
    forward = agents._event_forwarder(r, SimpleNamespace(phase_id="r1_01"), "builder", interface,
                                      [rule], can_refuse=can_refuse)
    return forward, events


def test_only_the_two_tool_events_are_forwarded_with_a_prefixed_id(run):
    calls = [{"id": "x", "toolName": "bash", "input": {"command": "ls"}}]
    code, out, err = run.go(FAKE_PI_CALLS=json.dumps(calls))
    assert code == 0, err
    events = forwarded(out)
    assert [e["type"] for e in events] == ["tool_execution_start", "tool_execution_end"]
    assert {e["toolCallId"] for e in events} == {"sub1:x"} and {e["subagent"] for e in events} == {1}


def test_a_childs_command_a_kill_rule_matches_stops_the_run(run):
    calls = [{"id": "k", "toolName": "bash", "input": {"command": "git push origin main"}}]
    code, out, err = run.go(FAKE_PI_CALLS=json.dumps(calls))
    assert code == 0, err
    forward, _ = stream_check(shipped_push_rule("stop"), can_refuse=True)
    with pytest.raises(rules.RuleBreach):
        for event in forwarded(out):
            forward(event)


def test_a_childs_blocked_refuse_match_counts_as_refused(run):
    calls = [{"id": "b", "toolName": "bash", "input": {"command": "git push origin main"}}]
    rule = shipped_push_rule("refuse")
    code, out, err = run.go(FAKE_PI_CALLS=json.dumps(calls),
                            SSSF_REFUSE_RULES=json.dumps(rules.refusing([rule])))
    assert code == 0, err
    forward, events = stream_check(rule, can_refuse=True)
    for event in forwarded(out):
        forward(event)
    assert [e.name for e in events] == ["rule_refused"]


def test_a_forwarded_line_reaches_fd_1_when_pi_has_taken_over_stdout(run):
    guard = None
    pi = shutil.which("pi")
    if pi:
        for parent in Path(pi).resolve().parents:
            if (parent / "dist" / "core" / "output-guard.js").is_file():
                guard = parent / "dist" / "core" / "output-guard.js"
                break
    if guard is None:
        pytest.skip("pi is not installed")
    calls = [{"id": "x", "toolName": "bash", "input": {"command": "ls"}}]
    code, out, err = run.go(FAKE_PI_CALLS=json.dumps(calls), DRIVER_GUARD=str(guard))
    assert code == 0, err
    assert [e["type"] for e in forwarded(out)] == ["tool_execution_start", "tool_execution_end"]
    assert "tool_execution" not in err


@pytest.mark.parametrize("how", ["throw", "callback", "event"])
def test_a_failed_stdout_write_kills_the_running_child(run, how):
    calls = [{"id": "x", "toolName": "bash", "input": {"command": "ls"}}]
    code, _, err = run.go(FAKE_PI_CALLS=json.dumps(calls), FAKE_PI_SLEEP="8000", DRIVER_FAIL=how)
    assert code == 0, err
    assert not run.marker.exists()          # killed before its sleep ended
    assert "Subagent #1" in result_text(run)


# ── the child is bound to the call ───────────────────────────────────────────

def test_under_sssf_the_tool_waits_for_the_child_and_returns_its_text(run):
    code, _, err = run.go(FAKE_PI_SLEEP="700")
    assert code == 0, err
    got = run.driver_result()
    assert got["markerAtReturn"] is True
    assert "child says hi" in result_text(run)
    assert got["sent"] == []
    assert "Waits for the subagent" in got["description"]


def test_without_sssf_the_tool_returns_at_once_and_sends_a_follow_up(run):
    done = run.go_plain(FAKE_PI_SLEEP="700")
    assert done.returncode == 0, done.stderr
    got = run.driver_result()
    assert got["markerAtReturn"] is False
    assert "running in background" in got["result"]["content"][0]["text"]
    assert [m["customType"] for m in got["sent"]] == ["subagent-result"]
    assert "Returns immediately" in got["description"]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_child_still_running_when_the_parent_exits_is_killed(run):
    proc = run.start(FAKE_PI_SLEEP="20000", DRIVER_EXIT_EARLY="1000")
    proc.communicate(timeout=30)
    pid = int(run.pidfile.read_text())
    deadline = time.time() + 5
    while alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    try:
        assert not alive(pid)
    finally:
        if alive(pid):
            os.kill(pid, signal.SIGKILL)
    assert not run.marker.exists()


# ── fail closed ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("change,reason", [
    ({"SSSF_REFUSE_EXTENSION": None, "SSSF_ADW_ID": "a1"}, "SSSF_REFUSE_EXTENSION"),
    ({"SSSF_REFUSE_EXTENSION": "rel/sssf_rules.ts"}, "absolute"),
    ({"SSSF_REFUSE_EXTENSION": "/no/such/file.ts"}, "regular file"),
    ({"SSSF_SESSION_DIR": None}, "SSSF_SESSION_DIR"),
    ({"SSSF_SESSION_DIR": "relative/dir"}, "SSSF_SESSION_DIR"),
])
def test_no_child_starts_when_the_extension_or_session_dir_is_wrong(run, change, reason):
    code, _, err = run.go(**change)
    assert code == 0, err
    assert not run.log.exists()
    assert reason in result_text(run) and "no subagent started" in result_text(run)
    assert not (run.session_dir / "subagents").exists()


@pytest.mark.parametrize("task", ["@.env", "  \n@.env please"])
def test_a_task_starting_with_at_starts_no_child(run, task):
    code, _, err = run.go(task=task)
    assert code == 0, err
    assert not run.log.exists()
    assert "@" in result_text(run)


# ── where the session goes ───────────────────────────────────────────────────

def test_the_session_goes_in_the_run_session_dir_and_nothing_in_home_pi(run):
    code, _, err = run.go(PI_CODING_AGENT_DIR=str(run.tmp / "agentdir"))
    assert code == 0, err
    argv = run.argv()["argv"]
    session = Path(argv[argv.index("--session") + 1])
    assert session.parent == run.session_dir / "subagents"
    assert (run.session_dir / "subagents").is_dir()
    assert not (run.home / ".pi").exists()


def test_without_sssf_the_session_is_in_home_and_the_argv_is_main_plus_two_flags(run):
    # --no-prompt-templates and --no-themes are the only difference from b752435's argv.
    done = run.go_plain()
    assert done.returncode == 0, done.stderr
    argv = run.argv()["argv"]
    session = Path(argv[argv.index("--session") + 1])
    assert session.parent == run.home / ".pi" / "agent" / "sessions" / "subagents"
    assert argv == ["--mode", "json", "-p", "--session", str(session), "--no-extensions",
                    "--no-prompt-templates", "--no-themes", "--model", "openai/gpt", "--tools", "read,bash,grep,find,ls",
                    "--thinking", "low", "look around"]
