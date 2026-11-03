"""An agent that writes nothing for max_silence_minutes is stopped.

A hung agent spends nothing, so no cost cap catches it, and it holds its run
and its worktree slot. silence.watch resets on every line the agent writes and,
once nothing has come for the limit, kills the agent's whole process tree; the
runner then raises SilentStop with what the call had spent.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agent_pi, repo_settings, session, silence
from adw_modules.data_types import AgentRequest


class FakeProcess:
    def __init__(self):
        self.stopped = 0

    def stop_tree(self):
        self.stopped += 1


# ── the timer ─────────────────────────────────────────────────────────────────

def test_it_stops_a_process_that_stays_quiet():
    process = FakeProcess()
    with silence.watch(process, 0.3, poll=0.05) as dog:
        time.sleep(0.6)
    assert dog.fired and process.stopped == 1


def test_each_line_resets_it():
    process = FakeProcess()
    with silence.watch(process, 0.3, poll=0.05) as dog:
        for _ in range(8):
            time.sleep(0.1)
            dog.touch()
    assert not dog.fired and process.stopped == 0


def test_no_limit_never_fires():
    process = FakeProcess()
    with silence.watch(process, None, poll=0.05) as dog:
        time.sleep(0.2)
    assert not dog.fired and process.stopped == 0


def test_the_setting_defaults_to_15_minutes_and_null_turns_it_off(tmp_path):
    assert repo_settings.load(tmp_path).max_silence_minutes == 15
    (tmp_path / "adws/adw_sssf_config").mkdir(parents=True)
    (tmp_path / repo_settings.PATH).write_text("max_silence_minutes: null\n")
    assert repo_settings.load(tmp_path).max_silence_minutes is None


def test_the_stop_names_the_agent_the_limit_and_the_resume():
    stop = silence.SilentStop("builder", 15, "ab12cd34")
    assert "builder wrote nothing for 15 minutes" in str(stop)
    assert "--adw-id ab12cd34" in str(stop)
    assert silence.SilentStop in session.STOPS


# ── the runners, with a fake agent that hangs ────────────────────────────────

# Writes its first line, starts a child that would outlive it, then hangs.
# The child's pid lands in a file, so the test can check the tree was killed.
HANG = """#!/usr/bin/env python3
import os, subprocess, sys, time
sys.stdout.write(os.environ["FAKE_FIRST_LINE"] + "\\n"); sys.stdout.flush()
child = subprocess.Popen(["sleep", "60"])
open(os.environ["FAKE_CHILD_PID"], "w").write(str(child.pid))
time.sleep(60)
"""

CLAUDE_INIT = '{"type":"system","subtype":"init","apiKeySource":"none","session_id":"x"}'
PI_TURN = ('{"type":"message_end","message":{"role":"assistant","stopReason":"stop",'
           '"content":[{"type":"text","text":"working"}],'
           '"usage":{"input":10,"output":5,"cost":{"total":0.01}}}}')


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child the fake never reaped stays a zombie until the fake dies.
    with open(f"/proc/{pid}/stat") as stat:
        return stat.read().split()[2] != "Z"


@pytest.fixture
def hang(tmp_path, monkeypatch):
    script = tmp_path / "agent"
    script.write_text(HANG)
    script.chmod(0o755)
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CHILD_PID", str(pid_file))

    def first(line: str) -> Path:
        monkeypatch.setenv("FAKE_FIRST_LINE", line)
        return pid_file

    first.script = script
    return first


def request(tmp_path, **fields) -> AgentRequest:
    base = {"prompt": "go", "system_prompt": "", "agent_name": "builder", "adw_id": "ab12cd34",
            "model": "anthropic/claude-haiku-5-5", "session_id": "f5e2a3a8-0d8e-4b8a-9a43-6c1f3e9b2d10",
            "session_dir": str(tmp_path / "sessions"),
            "raw_output_path": str(tmp_path / "raw.jsonl"), "cwd": str(tmp_path)}
    return AgentRequest(**{**base, **fields})


def test_a_quiet_claude_code_agent_is_stopped_with_its_tree(tmp_path, hang, monkeypatch):
    pid_file = hang(CLAUDE_INIT)
    monkeypatch.setattr(agent_cc, "CLAUDE_PATH", str(hang.script))
    monkeypatch.setattr(agent_cc, "AUTH", "subscription")
    began = time.monotonic()
    with pytest.raises(silence.SilentStop) as stop:
        agent_cc.run(request(tmp_path, max_silence_seconds=1))
    assert time.monotonic() - began < 15
    assert stop.value.agent == "builder" and stop.value.partial is not None
    time.sleep(0.2)
    assert not alive(int(pid_file.read_text()))


def test_a_quiet_pi_agent_is_stopped_and_keeps_what_it_spent(tmp_path, hang, monkeypatch):
    pid_file = hang(PI_TURN)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(hang.script))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    with pytest.raises(silence.SilentStop) as stop:
        agent_pi.run(request(tmp_path, model="openai/gpt", max_silence_seconds=1))
    assert stop.value.partial.cost == pytest.approx(0.01)
    time.sleep(0.2)
    assert not alive(int(pid_file.read_text()))


CHATTY = """#!/usr/bin/env python3
import os, sys, time
for _ in range(6):
    sys.stdout.write(os.environ["FAKE_LINE"] + "\\n"); sys.stdout.flush()
    time.sleep(0.4)
"""


def test_an_agent_that_keeps_writing_is_left_alone(tmp_path, monkeypatch):
    script = tmp_path / "pi"
    script.write_text(CHATTY)
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_LINE", PI_TURN)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    result = agent_pi.run(request(tmp_path, model="openai/gpt", max_silence_seconds=1))
    assert result.text == "working"
    assert result.cost == pytest.approx(0.06)


# ── an agent that has finished but stays alive ───────────────────────────────

# Claude Code waits for a background command it started before it exits, so a
# finished agent can sit quiet with its result already sent. Seen in
# sssf-sandbox run 95412cbf: scout answered, then waited on `sleep 240`.
LINGER = """#!/usr/bin/env python3
import os, subprocess, sys, time
for line in os.environ["FAKE_LINES"].split("\\n"):
    sys.stdout.write(line + "\\n")
sys.stdout.flush()
child = subprocess.Popen(["sleep", "60"])
open(os.environ["FAKE_CHILD_PID"], "w").write(str(child.pid))
time.sleep(60)
"""

CLAUDE_RESULT = ('{"type":"result","subtype":"success","is_error":false,"result":"done",'
                 '"total_cost_usd":0.02,"usage":{"input_tokens":10,"output_tokens":5}}')
PI_END = '{"type":"agent_end"}'


@pytest.fixture
def linger(tmp_path, monkeypatch):
    script = tmp_path / "agent"
    script.write_text(LINGER)
    script.chmod(0o755)
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CHILD_PID", str(pid_file))
    monkeypatch.setattr(silence, "GRACE", 0.5)
    return script, pid_file


def test_a_finished_claude_code_agent_keeps_its_result_and_its_leftovers_go(tmp_path, linger, monkeypatch):
    script, pid_file = linger
    monkeypatch.setenv("FAKE_LINES", CLAUDE_INIT + "\n" + CLAUDE_RESULT)
    monkeypatch.setattr(agent_cc, "CLAUDE_PATH", str(script))
    monkeypatch.setattr(agent_cc, "AUTH", "subscription")
    began = time.monotonic()
    result = agent_cc.run(request(tmp_path, max_silence_seconds=60))
    assert time.monotonic() - began < 10       # the grace, not the minute
    assert result.text == "done"
    time.sleep(0.2)
    assert not alive(int(pid_file.read_text()))


def test_a_finished_pi_agent_keeps_its_result(tmp_path, linger, monkeypatch):
    script, _ = linger
    monkeypatch.setenv("FAKE_LINES", PI_TURN + "\n" + PI_END)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    result = agent_pi.run(request(tmp_path, model="openai/gpt", max_silence_seconds=60))
    assert result.text == "working"


def test_finished_shortens_the_limit_to_the_grace(monkeypatch):
    monkeypatch.setattr(silence, "GRACE", 0.2)
    process = FakeProcess()
    with silence.watch(process, 60, poll=0.05) as dog:
        dog.finished()
        time.sleep(0.5)
    assert dog.fired and not dog.hung and process.stopped == 1
