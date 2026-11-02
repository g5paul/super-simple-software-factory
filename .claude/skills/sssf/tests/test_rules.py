"""agent_rules with `forbid`: a forbidden command stops the agent and the run.

The kill tests use real processes: a stand-in agent that announces a shell
call, starts the "command" as its own child, and waits. Stopping only the
agent would leave that child running.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agent_pi, repo_settings, rules
from adw_modules.data_types import AgentRequest

PUSH = rules.AgentRule(rule="Never run `git push`.", forbid=[r"\bgit\b.*\bpush\b"])


def settings(tmp_path: Path, body: str) -> Path:
    path = tmp_path / repo_settings.PATH
    path.parent.mkdir(parents=True)
    path.write_text(body)
    return tmp_path


def test_a_rule_is_a_string_or_a_mapping_and_every_agent_reads_both(tmp_path):
    root = settings(tmp_path, "agent_rules:\n  - Keep subjects short.\n"
                              "  - rule: Never run `git push`.\n    forbid: ['\\bgit\\b.*\\bpush\\b']\n")
    loaded = repo_settings.load(root).agent_rules
    assert loaded[0] == "Keep subjects short." and loaded[1].forbid == [r"\bgit\b.*\bpush\b"]
    assert repo_settings.with_agent_rules("SYSTEM", root).endswith(
        "- Keep subjects short.\n- Never run `git push`.\n")


def test_a_pattern_that_is_not_a_regex_fails_the_settings(tmp_path):
    root = settings(tmp_path, "agent_rules:\n  - rule: x\n    forbid: ['(']\n")
    with pytest.raises(ValidationError, match="not a valid regex"):
        repo_settings.load(root)


def test_shell_commands_are_read_from_either_harness():
    claude = {"type": "assistant", "parent_tool_use_id": "sub-1", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "git push origin"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}}]}}
    pi_announced = {"type": "message_end", "message": {"content": [
        {"type": "toolCall", "name": "bash", "arguments": {"command": "just fusion"}}]}}
    pi_started = {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}}
    assert rules.commands(claude) == ["git push origin"]       # a subagent's call counts too
    assert rules.commands(pi_announced) == ["just fusion"]
    assert rules.commands(pi_started) == ["ls"]
    assert rules.commands({"type": "result"}) == []


def test_only_a_rule_with_forbid_is_enforced():
    assert rules.breach(["Never run `git push`."], "git push") is None
    assert rules.breach([PUSH], "cd app && git -C . push origin main") == (PUSH, r"\bgit\b.*\bpush\b")
    assert rules.breach([PUSH], "git status") is None


AGENT = """import json, os, subprocess, sys, time
print(json.dumps(EVENT), flush=True)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(os.environ["CHILD_PID"], "w").write(str(child.pid))
time.sleep(60)
"""


def stand_in(tmp_path: Path, monkeypatch, event: dict) -> Path:
    """An agent binary that announces `event`, starts a child, and waits."""
    script = tmp_path / "agent"
    script.write_text(f"#!{sys.executable}\n" + AGENT.replace("EVENT", repr(event)))
    script.chmod(0o755)
    monkeypatch.setenv("CHILD_PID", str(tmp_path / "child.pid"))
    return script


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(")")[-1].split()[0] == "Z")


def enforce(agent_name="builder"):
    def on_event(event):
        for command in rules.commands(event):
            found = rules.breach([PUSH], command)
            if found:
                time.sleep(0.5)             # let the "command" start, as a real one would
                raise rules.RuleBreach(agent_name, *found, command)
    return on_event


def request(tmp_path: Path) -> AgentRequest:
    return AgentRequest(prompt="go", system_prompt="", model="anthropic/claude-sonnet-5-5",
                        session_id="f5e2a3a8-0d8e-4b8a-9a43-6c1f3e9b2d10",
                        session_dir=str(tmp_path / "sessions"),
                        raw_output_path=str(tmp_path / "raw.jsonl"), cwd=str(tmp_path))


def test_a_forbidden_command_kills_a_claude_agent_and_what_it_started(tmp_path, monkeypatch):
    event = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "git push"}}]}}
    monkeypatch.setattr(agent_cc, "CLAUDE_PATH", str(stand_in(tmp_path, monkeypatch, event)))
    exits = []
    start = time.monotonic()
    with pytest.raises(rules.RuleBreach, match="builder ran `git push`.*may have run"):
        agent_cc.run(request(tmp_path), on_event=enforce(), on_exit=exits.append)
    assert time.monotonic() - start < 20 and exits
    child = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not alive(child):
            break
        time.sleep(0.1)
    assert not alive(child)


def test_a_forbidden_command_kills_a_pi_agent_and_what_it_started(tmp_path, monkeypatch):
    event = {"type": "message_end", "message": {"role": "assistant", "content": [
        {"type": "toolCall", "name": "bash", "arguments": {"command": "git push"}}]}}
    monkeypatch.setattr(agent_pi, "PI_PATH", str(stand_in(tmp_path, monkeypatch, event)))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    exits = []
    with pytest.raises(rules.RuleBreach):
        agent_pi.run(request(tmp_path), on_event=enforce(), on_exit=exits.append)
    assert exits
    child = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not alive(child):
            break
        time.sleep(0.1)
    assert not alive(child)


def test_the_breach_message_names_the_agent_rule_and_command():
    error = rules.RuleBreach("builder", PUSH, r"\bgit\b.*\bpush\b", "git push origin main")
    assert (error.agent, error.rule, error.command) == ("builder", "Never run `git push`.", "git push origin main")
    assert json.dumps(str(error))
