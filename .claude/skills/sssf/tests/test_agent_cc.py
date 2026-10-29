"""Claude Code runner, tested against stream-json recorded from a real CLI.

The fixtures are two sends into ONE session: turn 1 creates it (Glob, Read,
and a failing Bash call), turn 2 resumes it. A fake `claude` replays them and
logs the argv and env it was handed, so no test touches the network.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
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
                          "has_api_key": "ANTHROPIC_API_KEY" in os.environ}) + "\\n")
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
