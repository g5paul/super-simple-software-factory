"""Claude Code runner, tested against stream-json recorded from a real CLI.

The fixtures are two sends into ONE session: turn 1 creates it (Glob, Read,
and a failing Bash call), turn 2 resumes it. A fake `claude` replays them and
logs the argv and env it was handed, so no test touches the network.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agents
from adw_modules.data_types import AgentConfig, AgentRequest, SSSFConfig

SESSION = "f5e2a3a8-0d8e-4b8a-9a43-6c1f3e9b2d10"

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CC_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:],
                          "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
                          "marks": [os.environ.get(k) for k in ("SSSF_ADW_ID", "SSSF_AGENT")],
                          "refuse": [os.environ.get(k) for k in ("CLAUDE_CODE_SHELL_PREFIX",
                                     "SSSF_REFUSE_RULES", "SSSF_NEXT_SHELL_PREFIX")]}) + "\\n")
sys.stdout.write(open(os.environ["FAKE_CC_FIXTURE"]).read())
sys.exit(int(os.environ.get("FAKE_CC_EXIT", "0")))
"""


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A fake claude on disk; returns a helper to point it at a fixture."""
    binary = tmp_path / "claude"
    binary.write_text(FAKE_CLAUDE)
    binary.chmod(0o755)
    log = tmp_path / "argv.jsonl"
    monkeypatch.setattr(agent_cc, "CLAUDE_PATH", str(binary))
    monkeypatch.setattr(agent_cc, "AUTH", "subscription")
    monkeypatch.setenv("FAKE_CC_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    def use(fixture: Path | str) -> list[dict]:
        monkeypatch.setenv("FAKE_CC_FIXTURE", str(fixture))
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    use.log = lambda: [json.loads(line) for line in log.read_text().splitlines()]
    return use


def request(tmp_path, **overrides) -> AgentRequest:
    fields = {"prompt": "count the adws", "system_prompt": "You are scout.",
              "model": "anthropic/claude-haiku-5-5", "thinking": "medium",
              "session_id": SESSION, "session_dir": str(tmp_path / "claude_code_sessions"),
              "raw_output_path": str(tmp_path / "raw_output.jsonl"),
              "tools": ["read", "bash", "grep", "find", "ls"], "cwd": str(tmp_path)}
    fields.update(overrides)
    return AgentRequest(**fields)


# ── translation of the roster into claude flags ──────────────────────────────

def test_model_id_strips_the_anthropic_provider():
    assert agent_cc.model_id("anthropic/claude-opus-5-5") == "claude-opus-5-5"
    assert agent_cc.model_id("claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert agent_cc.model_id("opus") == "opus"


def test_model_id_rejects_other_providers():
    with pytest.raises(ValueError, match="openai"):
        agent_cc.model_id("openai/gpt-5.6-terra")


def test_map_tools_translates_pi_names_and_dedupes():
    assert agent_cc.map_tools(["read", "bash", "find", "ls", "grep"]) == ["Read", "Bash", "Glob", "Grep"]


def test_map_tools_passes_claude_names_through():
    assert agent_cc.map_tools(["Read", "WebFetch"]) == ["Read", "WebFetch"]


def test_map_tools_none_means_the_default_set():
    assert agent_cc.map_tools(None) is None


def test_map_tools_rejects_pi_extension_tools():
    with pytest.raises(ValueError, match="subagent_create"):
        agent_cc.map_tools(["read", "subagent_create"])


@pytest.mark.parametrize("thinking, effort", [("off", "low"), ("minimal", "low"),
                                              ("medium", "medium"), ("max", "max")])
def test_thinking_maps_to_effort(thinking, effort):
    assert agent_cc.EFFORT[thinking] == effort


# ── one send ─────────────────────────────────────────────────────────────────

def test_first_send_creates_the_session_isolated(tmp_path, fake):
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path))
    argv = fake.log()[0]["argv"]

    assert argv[argv.index("--session-id") + 1] == SESSION
    assert "--resume" not in argv
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--tools") + 1] == "Read,Bash,Grep,Glob"
    assert argv[argv.index("--append-system-prompt") + 1] == "You are scout."
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--safe-mode" in argv            # no user CLAUDE.md, skills, plugins, MCP
    assert "--system-prompt" not in argv    # keep Claude Code's own tool guidance


def test_subscription_auth_strips_the_api_key(tmp_path, fake):
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path))
    assert fake.log()[0]["has_api_key"] is False


def test_the_agent_knows_it_is_an_sssf_agent(tmp_path, fake):
    # So a repo's own script can refuse a command before it starts.
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path, agent_name="builder", adw_id="d6af5329"))
    assert fake.log()[0]["marks"] == ["d6af5329", "builder"]


def test_api_key_auth_keeps_it(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(agent_cc, "AUTH", "api_key")
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path))
    assert fake.log()[0]["has_api_key"] is True


def test_result_reads_text_usage_and_context(tmp_path, fake):
    fake(FIXTURES / "cc_turn1.jsonl")
    result = agent_cc.run(request(tmp_path))

    assert result.text == '{"count": 12}'
    assert result.session_id == SESSION
    # result.usage covers this send: 4 + 3298 + 11860 + 273
    assert result.tokens == 15435
    assert result.usage.cache_read_tokens == 11860
    assert result.usage.cache_write_tokens == 3298
    assert result.cost == pytest.approx(0.0009151)
    assert result.usage.total_cost == pytest.approx(0.0009151)
    # occupancy is the LAST assistant turn, not a sum: 2 + 584 + 7287 + 8
    assert result.context_tokens == 7881
    assert result.context_window == 1_000_000


def test_raw_stream_lands_on_disk(tmp_path, fake):
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path))
    raw = (tmp_path / "raw_output.jsonl").read_text()
    assert raw == (FIXTURES / "cc_turn1.jsonl").read_text()


# ── the session continues ────────────────────────────────────────────────────

def test_second_send_resumes_and_bills_only_its_own_cost(tmp_path, fake):
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path))
    fake(FIXTURES / "cc_turn2.jsonl")
    result = agent_cc.run(request(tmp_path, prompt="what count?"))

    argv = fake.log()[1]["argv"]
    assert argv[argv.index("--resume") + 1] == SESSION
    assert "--session-id" not in argv
    # total_cost_usd is cumulative across a resumed session (0.00100971 after
    # turn 2); a send is billed only the difference.
    assert result.cost == pytest.approx(0.00100971 - 0.0009151)
    assert result.tokens == 2 + 56 + 7871 + 9


# ── tool calls ───────────────────────────────────────────────────────────────

def test_one_record_per_completed_tool_call(tmp_path, fake):
    fake(FIXTURES / "cc_turn1.jsonl")
    tracker = agent_cc.ToolCallTracker()
    records = []
    agent_cc.run(request(tmp_path), on_event=lambda e: records.extend(tracker.observe(e)))

    assert [r["tool"] for r in records] == ["Glob", "Read", "Bash"]
    assert [r["ok"] for r in records] == [True, True, False]
    assert records[0]["label"] == "Glob: adws/adw_*.py"
    assert records[2]["label"] == "Bash: false"
    assert records[2]["result_snippet"] == "Exit code 1"
    assert "adw_prompt.py" in records[0]["result_snippet"]
    for record in records:
        assert record["tool_call_id"].startswith("toolu_")
        assert record["started_at"] and record["ended_at"]
        assert record["duration_ms"] >= 0


def test_tool_result_content_may_be_a_block_list():
    tracker = agent_cc.ToolCallTracker()
    tracker.observe({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_x", "name": "Read", "input": {"file_path": "a.py"}}]}})
    [record] = tracker.observe({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_x",
         "content": [{"type": "text", "text": "line one"}]}]}})
    assert record["result_snippet"] == "line one"
    assert record["ok"] is True


def _stream(tmp_path, name, middle) -> tuple[Path, dict]:
    """cc_turn1's init and result lines around `middle`; no new fixture file."""
    lines = (FIXTURES / "cc_turn1.jsonl").read_text().splitlines()
    result = json.loads(lines[-1])
    path = tmp_path / name
    path.write_text("".join(json.dumps(e) + "\n" for e in [json.loads(lines[0]), *middle, result]))
    return path, result


def test_a_denied_read_is_a_failed_call_and_the_call_goes_on(tmp_path, fake):
    from adw_modules import rules
    usage = next(json.loads(line) for line in (FIXTURES / "cc_turn1.jsonl").read_text().splitlines()
                 if json.loads(line).get("type") == "assistant")["message"]["usage"]
    call_id = "toolu_01BdBmiNqfMBM4JPshZVxodW"
    path, result = _stream(tmp_path, "denied.jsonl", [
        {"type": "assistant", "message": {
            "id": "msg_denied", "model": "claude-haiku-5-5", "role": "assistant", "usage": usage,
            "content": [{"type": "tool_use", "id": call_id, "name": "Read",
                         "input": {"file_path": "/repo/.env"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "is_error": True,
             "content": "<tool_use_error>File is in a directory that is denied by your "
                        "permission settings.</tool_use_error>"}]}},
        {"type": "system", "subtype": "permission_denied", "tool_name": "Grep",
         "tool_use_id": call_id, "decision_reason_type": "rule",
         "message": "Permission to read /repo/.env has been denied.",
         "uuid": "bacf0074-14fc-4c40-9871-972f71ae02f7",
         "session_id": "2b34dc8b-a340-4c0c-82bb-046a7632da0a"},
        {"type": "system", "subtype": "permission_denied", "tool_name": "Bash",
         "tool_use_id": "toolu_011E3m58bgpqe7LGELGbwmZ2", "decision_reason_type": "rule",
         "message": "Permission to use Bash with command cat .env has been denied.",
         "session_id": "2b34dc8b-a340-4c0c-82bb-046a7632da0a"},
    ])
    fake(path)
    tracker = agent_cc.ToolCallTracker()
    records = []

    def on_event(event):
        records.extend(tracker.observe(event))
        rules.calls(event)
        rules.results(event)

    outcome = agent_cc.run(request(tmp_path), on_event=on_event)

    assert outcome.text == result["result"]
    assert len(records) == 1
    assert records[0]["tool"] == "Read"
    assert records[0]["ok"] is False
    assert "denied by your permission settings" in records[0]["result_snippet"]


def test_an_assistant_message_that_is_not_a_dict_does_not_raise(tmp_path, fake):
    tracker = agent_cc.ToolCallTracker()
    assert tracker.observe({"type": "assistant", "message": "oops"}) == []
    assert tracker.observe({"type": "user", "message": "oops"}) == []
    assert tracker.observe({"type": "system", "subtype": "permission_denied",
                            "message": "Permission to read /repo/.env has been denied."}) == []

    path, _ = _stream(tmp_path, "odd.jsonl", [
        {"type": "assistant", "message": "oops"},
        {"type": "stream_event", "event": {"type": "message_start", "message": "oops"}}])
    fake(path)
    agent_cc.run(request(tmp_path), on_event=lambda e: tracker.observe(e))


# ── failures ─────────────────────────────────────────────────────────────────

def _rewrite(tmp_path, name, edit) -> Path:
    lines = [json.loads(line) for line in (FIXTURES / "cc_turn1.jsonl").read_text().splitlines()]
    path = tmp_path / name
    path.write_text("".join(json.dumps(edit(event)) + "\n" for event in lines))
    return path


def test_subscription_run_refuses_api_billing(tmp_path, fake):
    def billed(event):
        if event.get("subtype") == "init":
            event["apiKeySource"] = "ANTHROPIC_API_KEY"
        return event
    fake(_rewrite(tmp_path, "billed.jsonl", billed))
    with pytest.raises(RuntimeError, match="subscription"):
        agent_cc.run(request(tmp_path))


def test_error_result_raises(tmp_path, fake):
    def errored(event):
        if event.get("type") == "result":
            event.update(subtype="error_max_turns", is_error=True, result="")
        return event
    fake(_rewrite(tmp_path, "errored.jsonl", errored))
    with pytest.raises(RuntimeError, match="error_max_turns"):
        agent_cc.run(request(tmp_path))


def test_crash_without_result_raises(tmp_path, fake, monkeypatch):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    monkeypatch.setenv("FAKE_CC_EXIT", "1")
    fake(empty)
    with pytest.raises(RuntimeError, match="exited 1"):
        agent_cc.run(request(tmp_path))


# ── validation ───────────────────────────────────────────────────────────────

def _cfg(tmp_path, **agent) -> SSSFConfig:
    for name in ("system.md", "user.md"):
        (tmp_path / name).write_text("x")
    return SSSFConfig(agents=[AgentConfig(
        name="scout", coding_agent="claude_code", model="anthropic/claude-haiku-5-5",
        prompt_engineering={"system": str(tmp_path / "system.md"),
                            "user": str(tmp_path / "user.md")}, **agent)])


def test_validate_accepts_a_claude_code_agent(tmp_path):
    agents.validate(_cfg(tmp_path, tools=["read", "bash"]), ["scout"])


def test_validate_rejects_pi_extensions_on_claude_code(tmp_path):
    cfg = _cfg(tmp_path, harness_engineering=["adws/adw_data/harness_engineering/subagents.ts"])
    with pytest.raises(SystemExit, match="harness_engineering"):
        agents.validate(cfg, ["scout"])


def test_validate_rejects_unmappable_tools(tmp_path):
    with pytest.raises(SystemExit, match="subagent_list"):
        agents.validate(_cfg(tmp_path, tools=["read", "subagent_list"]), ["scout"])


def test_validate_rejects_non_anthropic_models(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.agents[0].model = "google/gemini-3.6-flash"
    with pytest.raises(SystemExit, match="google"):
        agents.validate(cfg, ["scout"])


# ── login check ─────────────────────────────────────────────────────────────

FAKE_AUTH = """#!/usr/bin/env python3
import json, os, sys
logged_in = "FAKE_TOKEN" in os.environ
print(json.dumps({"loggedIn": logged_in, "authMethod": "oauth_token" if logged_in else "none"}))
sys.exit(0 if logged_in else 1)
"""


@pytest.fixture
def auth(tmp_path, monkeypatch):
    binary = tmp_path / "claude"
    binary.write_text(FAKE_AUTH)
    binary.chmod(0o755)
    monkeypatch.setattr(agent_cc, "CLAUDE_PATH", str(binary))
    monkeypatch.setattr(agent_cc, "_logged_in", False)


def test_no_login_stops_with_the_fix(auth, monkeypatch):
    monkeypatch.delenv("FAKE_TOKEN", raising=False)
    with pytest.raises(agent_cc.NotLoggedIn, match="CLAUDE_CODE_OAUTH_TOKEN"):
        agent_cc.check_login()


def test_a_login_is_checked_once(auth, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_TOKEN", "1")
    agent_cc.check_login()
    (tmp_path / "claude").write_text("#!/bin/sh\nexit 1\n")
    agent_cc.check_login()


def test_not_logged_in_is_a_stop():
    from adw_modules import session
    assert agent_cc.NotLoggedIn in session.STOPS


def test_a_stop_mid_call_stops_the_agent_before_waiting_for_it(tmp_path, fake, monkeypatch):
    # In a sandbox VM, `just kill` can't reach the agent, so the runner must,
    # or the chain waits for the agent to finish before it can exit.
    from adw_modules import sandbox
    fake(FIXTURES / "cc_turn1.jsonl")
    stopped = []

    class Stopping(sandbox.Local):
        def start(self, command, **kwargs):
            process = super().start(command, **kwargs)
            process.stop_tree = lambda: stopped.append(True)
            return process

    monkeypatch.setattr(sandbox, "_current", Stopping())

    def killed(event):
        raise SystemExit(143)

    with pytest.raises(SystemExit):
        agent_cc.run(request(tmp_path), on_event=killed)
    assert stopped == [True]


# ── refuse rules ─────────────────────────────────────────────────────────────

RULES = [{"rule": "Never run `git push`.", "forbid": [r"\bgit\s+push\b"]}]


def test_refuse_rules_route_bash_through_sssfs_shell_prefix(tmp_path, fake, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SHELL_PREFIX", raising=False)
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path, refuse_rules=RULES))
    prefix, passed, following = fake.log()[0]["refuse"]
    assert prefix == str(agent_cc.REFUSE_SHELL) and json.loads(passed) == RULES
    assert following is None
    assert os.access(agent_cc.REFUSE_SHELL, os.X_OK)


def test_no_refuse_rules_leaves_the_shell_alone(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SHELL_PREFIX", "/opt/audit.sh")
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path))
    assert fake.log()[0]["refuse"] == ["/opt/audit.sh", None, None]


def test_an_operators_own_prefix_runs_after_sssfs(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SHELL_PREFIX", "/opt/audit.sh")
    fake(FIXTURES / "cc_turn1.jsonl")
    agent_cc.run(request(tmp_path, refuse_rules=RULES))
    prefix, _, following = fake.log()[0]["refuse"]
    assert (prefix, following) == (str(agent_cc.REFUSE_SHELL), "/opt/audit.sh")


def test_a_copy_without_the_exec_bit_gets_it_back(tmp_path, monkeypatch):
    from adw_modules import sandbox
    script = tmp_path / "sssf_refuse.py"
    script.write_text(agent_cc.REFUSE_SHELL.read_text())
    script.chmod(0o644)
    sandbox.LOCAL.install(script)
    assert os.access(script, os.X_OK)
