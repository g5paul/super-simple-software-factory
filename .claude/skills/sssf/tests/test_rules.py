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
import shlex
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agent_pi, agents, repo_settings, rules, secret_files
from adw_modules.data_types import AgentRequest

PUSH = rules.AgentRule(rule="Never run `git push`.", forbid=[r"\bgit\b(\s+-[cC]\s+\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b"])


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
    assert rules.breach([PUSH], "cd app && git -C . push origin main") == (PUSH, PUSH.forbid[0])


def test_the_documented_push_pattern_spares_local_git_work():
    for command in ("git push", "git -c a=b push --force", "git --no-pager push", "cd x && git push"):
        assert rules.breach([PUSH], command), command
    for command in ("git status", "git stash push -m wip", "git log --grep push",
                    "git commit -m 'no push here'"):
        assert rules.breach([PUSH], command) is None, command


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
    # The process can be reaped between the kill and the read; reading a
    # vanished process's stat raises ProcessLookupError (ESRCH) or
    # FileNotFoundError, and either means it's gone.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


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
    event = {"type": "assistant", "message": {"id": "msg_1", "model": "claude-opus-5-5", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "git push"}}],
        "usage": {"input_tokens": 100, "output_tokens": 20}}}
    monkeypatch.setattr(agent_cc, "CLAUDE_PATH", str(stand_in(tmp_path, monkeypatch, event)))
    exits = []
    start = time.monotonic()
    with pytest.raises(rules.RuleBreach, match="builder ran `git push`.*may have run") as stop:
        agent_cc.run(request(tmp_path), on_event=enforce(), on_exit=exits.append)
    assert time.monotonic() - start < 20 and exits
    # Claude prices a call only in its final result, which a killed call never
    # sends, so SSSF prices its tokens at list price: Opus at $4 in, $20 out.
    partial = stop.value.partial
    assert partial.usage.input_tokens == 100 and partial.usage.output_tokens == 20
    assert partial.cost == pytest.approx(100 * 4e-6 + 20 * 20e-6)
    assert partial.cost_reported is False
    child = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not alive(child):
            break
        time.sleep(0.1)
    assert not alive(child)


def test_a_forbidden_command_kills_a_pi_agent_and_what_it_started(tmp_path, monkeypatch):
    event = {"type": "message_end", "message": {"role": "assistant", "content": [
        {"type": "toolCall", "name": "bash", "arguments": {"command": "git push"}}],
        "usage": {"input": 100, "output": 20, "cost": {"total": 0.004}}}}
    monkeypatch.setattr(agent_pi, "PI_PATH", str(stand_in(tmp_path, monkeypatch, event)))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    exits = []
    with pytest.raises(rules.RuleBreach) as stop:
        agent_pi.run(request(tmp_path), on_event=enforce(), on_exit=exits.append)
    assert exits
    assert stop.value.partial.cost == pytest.approx(0.004)    # pi prices every turn
    child = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not alive(child):
            break
        time.sleep(0.1)
    assert not alive(child)


def test_the_breach_message_names_the_agent_rule_and_command():
    error = rules.RuleBreach("builder", PUSH, PUSH.forbid[0], "git push origin main")
    assert (error.agent, error.rule, error.command) == ("builder", "Never run `git push`.", "git push origin main")
    assert json.dumps(str(error))


# ── on_match: refuse ─────────────────────────────────────────────────────────

REFUSE = PUSH.model_copy(update={"on_match": "refuse"})


def forwarder(can_refuse=True, rule=REFUSE, enforced=None, uncounted=()):
    events, said = [], []
    run = SimpleNamespace(adw_id="r1", tracer=SimpleNamespace(event=events.append),
                          console=SimpleNamespace(advise=said.append))
    interface = SimpleNamespace(ToolCallTracker=lambda: SimpleNamespace(observe=lambda e: []))
    forward = agents._event_forwarder(run, SimpleNamespace(phase_id="r1_01"), "builder",
                                      interface, [rule] if enforced is None else enforced,
                                      can_refuse=can_refuse, uncounted=uncounted)
    return forward, events, said


def announce(call_id, command="git push"):
    return {"type": "tool_execution_start", "toolCallId": call_id, "toolName": "bash",
            "args": {"command": command}}


def starter_push_rule():
    """The push rule from the starter's commented agent_rules in templates/repo.yaml, as shipped."""
    lines = (SKILL / "templates" / "repo.yaml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("#     - rule: Never run `git push`"))
    body = "\n".join(line[2:] for line in lines[start:start + 3])
    return rules.AgentRule(**{**yaml.safe_load(body)[0], "on_match": "refuse"})


def finish(call_id, text):
    """pi's result for a call it never ran: isError, no durationMs (a `tool_call` block)."""
    return {"type": "tool_execution_end", "toolCallId": call_id, "toolName": "bash",
            "result": {"content": [{"type": "text", "text": text}]}, "isError": True}


def ran(call_id, text, is_error=False):
    """pi's result for a call it ran: the same event with durationMs."""
    return {**finish(call_id, text), "isError": is_error, "durationMs": 12}


def test_a_refused_command_is_logged_and_the_run_goes_on():
    forward, events, said = forwarder()
    forward(announce("c1"))
    forward(finish("c1", f"{rules.REFUSED}, by this repo's rule: Never run `git push`."))
    assert [(e.name, e.payload["command"], e.payload["refusals"]) for e in events] == [
        ("rule_refused", "git push", 1)]
    assert said and "git push" in said[0]


def test_a_refuse_match_whose_result_is_not_the_refusal_ran_and_stops_the_run():
    forward, _, _ = forwarder()
    forward(announce("c1"))
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(finish("c1", "Everything up-to-date"))


def test_the_third_refusal_stops_the_run_and_says_nothing_ran():
    forward, _, _ = forwarder()
    for n in range(1, rules.MAX_REFUSALS):
        forward(announce(f"c{n}"))
        forward(finish(f"c{n}", rules.REFUSED))
    forward(announce("last"))
    with pytest.raises(rules.RuleBreach, match="Nothing refused ran") as stop:
        forward(finish("last", rules.REFUSED))
    assert stop.value.refusals == rules.MAX_REFUSALS


def test_denied_reads_only_claude_codes_permission_denied_event():
    event = {"type": "system", "subtype": "permission_denied", "tool_name": "Bash",
             "tool_use_id": "toolu_011E3m58bgpqe7LGELGbwmZ2", "decision_reason_type": "rule",
             "message": "Permission to use Bash with command cat .env has been denied."}
    assert rules.denied(event) == ["toolu_011E3m58bgpqe7LGELGbwmZ2"]
    for other in ({"type": "system", "subtype": "init"},
                  {k: v for k, v in event.items() if k != "tool_use_id"},
                  {**event, "tool_use_id": None}, {**event, "tool_use_id": 7},
                  {"type": "user", "message": {"content": [
                      {"type": "tool_result", "tool_use_id": "c1", "content": event["message"]}]}},
                  finish("c1", "x")):
        assert rules.denied(other) == []
    forward, events, _ = forwarder()
    forward(announce("c1"))
    forward({"type": "system", "subtype": "permission_denied", "tool_use_id": "c1"})
    forward(finish("c1", "Permission denied"))
    assert [(e.name, e.payload["refusals"]) for e in events] == [("rule_refused", 1)]


def test_a_harness_that_cannot_refuse_stops_at_the_announcement():
    forward, _, _ = forwarder(can_refuse=False)
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(announce("c1"))


def test_results_are_read_from_either_harness():
    pi_message = {"type": "message_end", "message": {"role": "toolResult", "toolCallId": "c1",
                                                     "content": [{"type": "text", "text": "no"}]}}
    claude = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]}]}}
    assert rules.results(pi_message) == [("c1", "no")]
    assert rules.results(claude) == [("t1", "ok")]
    assert rules.results(finish("c2", "x")) == [("c2", "x")]


def test_pi_always_loads_the_refusing_extension_and_passes_rules_only_when_set(tmp_path, monkeypatch):
    seen = tmp_path / "seen"
    script = tmp_path / "pi"
    script.write_text(f'#!/bin/sh\necho "$@" > {seen}\nprintf "%s\\n" "${{SSSF_REFUSE_RULES:-none}}" >> {seen}\n')
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    for refuse, expect in ((rules.refusing([REFUSE, PUSH, "plain"]), True), ([], False)):
        req = request(tmp_path).model_copy(update={"refuse_rules": refuse})
        try:
            agent_pi.run(req)
        except RuntimeError:
            pass                    # the stand-in prints no turn
        argv, env = seen.read_text().splitlines()
        assert str(agent_pi.REFUSE_EXTENSION) in argv
        assert (json.loads(env) == [{"rule": REFUSE.rule, "forbid": REFUSE.forbid}]) if expect else env == "none"


def test_the_refuse_extension_loads_after_every_user_extension(tmp_path, monkeypatch):
    from adw_modules import sandbox
    seen = tmp_path / "seen"
    script = tmp_path / "pi"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {seen}\n')
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    req = request(tmp_path).model_copy(update={"refuse_rules": rules.refusing([REFUSE]),
                                               "extensions": ["first.ts", "second.ts"]})
    want = ["first.ts", "second.ts", str(agent_pi.REFUSE_EXTENSION)]

    def loaded(argv):
        return [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]

    try:
        agent_pi.run(req)
    except RuntimeError:
        pass                        # the stand-in prints no turn
    assert loaded(seen.read_text().splitlines()) == want

    class Stop(Exception):
        pass

    got = {}

    def start(command, env=None, **kw):
        got["command"], got["env"] = command, env
        raise Stop

    asked = []
    vm = SimpleNamespace(install=lambda path: None, start=start, name="vercel",
                         relay_obs=asked.append)
    monkeypatch.setattr(sandbox, "current", lambda: vm)
    monkeypatch.setattr(agent_pi, "vm_token", lambda: "eyJ-access")
    for name in ("OBS_DISABLE", "OBS_SERVER_URL", "OBS_TAG", "OBS_POOL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OBS_AUTH_TOKEN", "secret")
    with pytest.raises(Stop):
        agent_pi.run(req)
    assert loaded(got["command"]) == want
    assert got["env"]["OBS_DISABLE"] == "true" and "OBS_AUTH_TOKEN" not in got["env"] \
        and "secret" not in json.dumps(got)
    assert [t.token for t in asked] == ["secret"]


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_the_pi_extension_blocks_a_matching_bash_call_with_the_rule(tmp_path):
    check = tmp_path / "check.ts"
    check.write_text(f'''
import ext from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
let handler: any;
ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
const out = [
  handler({{ toolName: "bash", input: {{ command: "git -C . push origin main" }} }}),
  handler({{ toolName: "bash", input: {{ command: "git stash push -m wip" }} }}),
  handler({{ toolName: "read", input: {{ path: "git push" }} }}),
];
console.log(JSON.stringify(out));
''')
    env = {**os.environ, "SSSF_REFUSE_RULES": json.dumps(rules.refusing([REFUSE]))}
    done = subprocess.run(["bun", str(check)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    blocked, spared, other = json.loads(done.stdout)
    assert blocked["block"] is True and blocked["reason"].startswith(rules.REFUSED)
    assert REFUSE.rule in blocked["reason"]
    assert blocked["reason"] == (
        f"{rules.REFUSED}, by this repo's rule: {REFUSE.rule} (it matches {json.dumps(REFUSE.forbid[0])}). "
        "Nothing ran. Don't try it another way: do the task without it, or say in your report "
        "that the task needs it.")
    assert spared is None and other is None


def test_the_prefix_names_the_built_in_rule_as_sssfs():
    builtin = secret_files.shell_rule(["/r/.env"])
    done = prefix(claude_line("cat .env"), json.dumps(rules.refusing([builtin], builtin=builtin)))
    assert done.returncode == 126
    assert done.stderr.startswith(
        rules.REFUSED + ", by SSSF's secret-file rule: " + secret_files.RULE_TEXT + " Nothing ran.")
    assert "this repo's rule" not in done.stderr
    assert rules.refused(f"Exit code 126\n{done.stderr}")
    twin = builtin.model_copy()
    done = prefix(claude_line("cat .env"), json.dumps(rules.refusing([twin], builtin=builtin)))
    assert ", by this repo's rule: " in done.stderr


def test_the_prefix_keeps_a_repo_rules_wording():
    done = prefix(claude_line("git push"), json.dumps(rules.refusing([REFUSE])))
    assert done.stderr == (
        f"{rules.REFUSED}, by this repo's rule: {REFUSE.rule} (it matches {json.dumps(REFUSE.forbid[0])}). "
        "Nothing ran. Don't try it another way: do the task without it, or say in your report "
        "that the task needs it.\n")


def test_refusing_marks_only_the_built_in_rule():
    builtin = secret_files.shell_rule(["/r/.env"])
    twin = builtin.model_copy()
    assert rules.refusing([REFUSE, builtin, twin], builtin=builtin) == [
        {"rule": REFUSE.rule, "forbid": REFUSE.forbid},
        {"rule": builtin.rule, "forbid": builtin.forbid, "builtin": True},
        {"rule": twin.rule, "forbid": twin.forbid}]
    assert all("builtin" not in r for r in rules.refusing([REFUSE]))


def test_a_breach_message_names_the_built_in_rule_as_sssfs():
    builtin = secret_files.shell_rule(["/r/.env"])
    text = str(rules.RuleBreach("builder", builtin, builtin.forbid[0], "cat .env", builtin=True))
    assert "which SSSF's secret-file rule forbids: " in text and "this repo's rule" not in text
    text = str(rules.RuleBreach("builder", builtin, builtin.forbid[0], "cat .env", refusals=3, builtin=True))
    assert "times for SSSF's secret-file rule: " in text
    assert str(rules.RuleBreach("builder", PUSH, PUSH.forbid[0], "git push origin main")) == (
        f"builder ran `git push origin main`, which this repo's rule forbids: {PUSH.rule} "
        f"(matched {PUSH.forbid[0]!r}). SSSF killed the agent and everything it started, but the "
        "command may have run first: check what it did before resuming.")
    assert str(rules.RuleBreach("builder", PUSH, PUSH.forbid[0], "git push origin main", refusals=3)) == (
        f"SSSF refused builder 3 times for this repo's rule: {PUSH.rule} "
        f"(matched {PUSH.forbid[0]!r}; last `git push origin main`). Nothing refused ran. SSSF "
        "stopped the agent so it doesn't keep trying: give it another way, or do that step yourself.")


def test_a_file_tool_refusal_never_reaches_the_stream_check():
    builtin = secret_files.shell_rule(["/r/.env"])
    forward, events, _ = forwarder(enforced=[builtin], uncounted=(builtin,))
    forward({"type": "tool_execution_start", "toolCallId": "r1", "toolName": "read",
             "args": {"path": ".env"}})
    forward({"type": "tool_execution_end", "toolCallId": "r1", "toolName": "read",
             "result": {"content": [{"type": "text", "text": (
                 "SSSF refused this read call: .env is one of this repo's secret files, which SSSF "
                 "keeps from agents. Nothing was read.")}]}, "isError": True})
    assert events == []


def test_a_claude_code_refusal_is_read_past_its_exit_line():
    forward, events, _ = forwarder()
    claude_call = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git push"}}]}}
    result = lambda text: {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": text}]}}
    forward(claude_call)
    forward(result(f"Exit code 126\n{rules.REFUSED}, by this repo's rule: x"))
    assert [e.name for e in events] == ["rule_refused"]
    forward(claude_call)
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(result(f"Exit code 1\n{rules.REFUSED}"))


def claude_call(call_id="t1", command="git push"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}]}}


def test_blocked_reads_only_pis_never_ran_results():
    assert rules.blocked(finish("c1", "x")) == ["c1"]
    assert rules.blocked(ran("c1", "x", is_error=True)) == []
    assert rules.blocked({**finish("c1", "x"), "isError": False}) == []

    def message(**extra):
        return {"type": "message_end", "message": {"role": "toolResult", "toolCallId": "c2",
                                                   "content": [], **extra}}
    assert rules.blocked(message(isError=True)) == ["c2"]
    assert rules.blocked(message(isError=True, durationMs=3)) == []
    assert rules.blocked(message(isError=False)) == []
    claude_result = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
         "content": f"Exit code 126\n{rules.REFUSED}"}]}}
    assert rules.blocked(claude_result) == []
    assert not rules.pi_result(claude_result)
    assert rules.blocked({"type": "system", "subtype": "permission_denied", "tool_use_id": "c1"}) == []


@pytest.mark.parametrize("text", [rules.REFUSED, "Exit code 126\n" + rules.REFUSED])
@pytest.mark.parametrize("is_error", [True, False])
def test_a_pi_command_that_ran_and_printed_the_refusal_stops_the_run(text, is_error):
    forward, events, _ = forwarder(rule=starter_push_rule())
    forward(announce("c1"))
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(ran("c1", text, is_error))
    assert events == []
    forward, events, _ = forwarder(rule=starter_push_rule())
    forward(announce("c2"))
    message = {"type": "message_end", "message": {"role": "toolResult", "toolCallId": "c2",
                                                  "isError": is_error, "durationMs": 12,
                                                  "content": [{"type": "text", "text": text}]}}
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(message)
    assert events == []


def test_a_pi_built_in_match_that_ran_and_printed_the_refusal_stops_the_run():
    builtin = secret_files.shell_rule(["/r/.env"])
    forward, events, _ = forwarder(enforced=[builtin], uncounted=(builtin,))
    forward(announce("c1", "cat .env"))
    with pytest.raises(rules.RuleBreach) as stop:
        forward(ran("c1", rules.REFUSED))
    assert stop.value.builtin is True
    assert events == []


def test_a_pending_refuse_call_at_exit_is_a_breach():
    forward, events, _ = forwarder(rule=starter_push_rule())
    forward(announce("c1"))
    with pytest.raises(rules.RuleBreach, match="may have run") as stop:
        forward.ended()
    assert stop.value.refusals == 0
    marker = object()
    with pytest.raises(rules.RuleBreach) as stop:
        forward.ended(partial=marker)
    assert stop.value.partial is marker
    # A refused call that finished leaves nothing pending.
    forward, _, _ = forwarder(rule=starter_push_rule())
    forward(announce("c1"))
    forward(finish("c1", f"{rules.REFUSED}, by this repo's rule: x"))
    assert forward.ended() is None
    # Same on Claude Code.
    forward, _, _ = forwarder(rule=starter_push_rule())
    forward(claude_call())
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward.ended()


@pytest.mark.parametrize("stop", ["error", "aborted"])
def test_a_call_in_a_turn_pi_ended_in_error_is_not_held_for_exit(stop):
    builtin = secret_files.shell_rule(["/r/.env"])
    forward, events, _ = forwarder(enforced=[builtin], uncounted=(builtin,))
    command = "cat > notes.md <<'EOF'\nKeys live in .env; never print them.\nEOF"
    forward({"type": "message_end", "message": {
        "role": "assistant", "stopReason": stop, "content": [
            {"type": "toolCall", "id": "e1", "name": "bash", "arguments": {"command": command}}]}})
    assert forward.ended() is None
    # The same message in a normal turn is still held.
    forward, _, _ = forwarder(enforced=[builtin], uncounted=(builtin,))
    forward({"type": "message_end", "message": {
        "role": "assistant", "stopReason": "toolUse", "content": [
            {"type": "toolCall", "id": "e1", "name": "bash", "arguments": {"command": command}}]}})
    with pytest.raises(rules.RuleBreach):
        forward.ended()


@pytest.mark.parametrize("dies", ["raises", "returns"])
def test_execute_stops_when_the_agent_dies_with_a_refuse_call_pending(tmp_path, monkeypatch, dies):
    from adw_modules.data_types import AgentResult
    from test_secret_files import make_repo, shipped_config
    cfg = shipped_config()
    root = make_repo(tmp_path / "repo")
    monkeypatch.chdir(root)
    data = yaml.safe_load((SKILL / "templates" / "repo.yaml").read_text()) or {}
    data["agent_rules"] = [{"rule": "Never run `git push`.", "forbid": [r"\bgit\s+push\b"],
                            "on_match": "refuse"}]
    path = root / repo_settings.PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    for a in cfg.agents:
        for ref in (a.prompt_engineering.system, a.prompt_engineering.user):
            f = root / ref
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch()

    def run_fake(request, **kw):
        kw["on_event"](announce("c1", "git push"))
        if dies == "raises":
            raise RuntimeError("pi exited 137: ")
        return AgentResult(text="done", cost=0.1)

    fake = SimpleNamespace(run=run_fake, ToolCallTracker=agent_pi.ToolCallTracker)
    monkeypatch.setattr(agents, "_interface", lambda agent: fake)
    for name in ("check_run", "check_live", "check_daily", "check_machine"):
        monkeypatch.setattr(agents.budget, name, lambda *a, **k: None)
    monkeypatch.setattr(agents.permissions, "guard_git_dir", lambda run: None)
    monkeypatch.setattr(agents.permissions, "restore_git_dir", lambda *a, **k: [])
    monkeypatch.setattr(agents.agent_cc, "check_login", lambda *a, **k: None)
    monkeypatch.setattr(agents.account, "check", lambda *a, **k: None)
    seen = []
    run = SimpleNamespace(
        cfg=cfg, adw_id="r1", session_dir=tmp_path / "s", repo_root=root,
        context_handoff_dir=tmp_path / "h", agent_map={},
        tracer=SimpleNamespace(event=seen.append, process_start=lambda *a: None,
                               process_end=lambda *a: None, gate_row=lambda *a: None,
                               agent_session_row=lambda *a, **k: None),
        console=SimpleNamespace(agent_started=lambda *a: None, advise=lambda *a: None,
                                note=lambda *a: None, envelope_summary=lambda *a: None),
        add_usage=lambda *a: None, save_agent_map=lambda *a: None)
    phase = SimpleNamespace(phase_id="r1_01", params=SimpleNamespace(owner="builder", name="build"))
    call = SimpleNamespace(prompt="x", previous=None, session=None)
    with pytest.raises(rules.RuleBreach):
        agents.execute(run, phase, call)
    [breach] = [e for e in seen if e.name == "rule_breach"]
    assert breach.type == "error" and breach.payload["command"] == "git push"


# ── sssf_refuse.py, the Claude Code shell prefix ─────────────────────────────

def claude_line(command):
    """The shell line Claude Code hands its prefix (2.1.317)."""
    return ("source /home/u/.claude/shell-snapshots/snapshot-bash-1.sh 2>/dev/null || true && "
            "{ shopt -u extglob || setopt NO_EXTENDED_GLOB NO_BARE_GLOB_QUAL; } >/dev/null 2>&1 || true"
            f" && eval {shlex.quote(command)} < /dev/null && pwd -P >| /tmp/claude-ab12-cwd")


def prefix(line, rules_json, **env):
    return subprocess.run([sys.executable, str(agent_cc.REFUSE_SHELL), line], capture_output=True,
                          text=True, env={**os.environ, "SSSF_REFUSE_RULES": rules_json, **env})


def test_the_prefix_refuses_a_matching_command_and_nothing_runs(tmp_path):
    marker = tmp_path / "ran"
    done = prefix(claude_line(f"git -C . push origin main; touch {marker}"),
                  json.dumps(rules.refusing([REFUSE])))
    assert done.returncode == 126 and done.stderr.startswith(rules.REFUSED)
    assert REFUSE.rule in done.stderr and not marker.exists()
    assert rules.refused(f"Exit code 126\n{done.stderr}")


def test_the_prefix_runs_anything_else_as_claude_meant(tmp_path):
    done = prefix(claude_line("git stash list >/dev/null 2>&1; echo it\\'s fine"),
                  json.dumps(rules.refusing([REFUSE])))
    assert done.returncode == 0 and "it's fine" in done.stdout


def test_the_prefix_searches_a_line_of_another_shape_whole():
    done = prefix("git push origin main", json.dumps(rules.refusing([REFUSE])))
    assert done.returncode == 126


def test_the_prefix_skips_a_pattern_python_cannot_compile_and_bad_rules_json():
    bad = json.dumps([{"rule": "r", "forbid": ["("]}])
    assert prefix(claude_line("echo ok"), bad).returncode == 0
    assert prefix(claude_line("echo ok"), "not json").returncode == 0


def test_the_prefix_hands_on_to_an_operators_own(tmp_path):
    audit = tmp_path / "audit.sh"
    audit.write_text(f'#!/bin/sh\necho "$1" > {tmp_path}/audited\nexec bash -c "$1"\n')
    audit.chmod(0o755)
    done = prefix(claude_line("echo hi"), "[]", SSSF_NEXT_SHELL_PREFIX=str(audit))
    assert done.stdout.strip().endswith("hi") and "eval" in (tmp_path / "audited").read_text()


def test_an_anchored_pattern_finds_the_command_in_a_line_of_another_shape():
    # From a project's session: `gh` follows `eval '`, so the whole line alone misses it.
    anchored = json.dumps([{"rule": "No gh writes.", "forbid": [r"(^|[;&|(`]\s*)gh\s+pr\s+create\b"]}])
    line = f"source snap.sh; eval {shlex.quote('gh pr create --fill')}; pwd -P"
    assert prefix(line, anchored).returncode == 126
    assert prefix(claude_line("gh pr create --fill"), anchored).returncode == 126
    assert prefix(claude_line("echo gh pr create"), anchored).returncode == 0


# ── pi in a remote VM (docs/research/pi-chatgpt-in-vm.md) ────────────────────

def fake_pi(tmp_path, monkeypatch, body):
    script = tmp_path / "pi"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    return script


def test_the_vm_token_comes_from_the_hosts_sign_in_with_35_minutes_left(tmp_path, monkeypatch):
    fake_pi(tmp_path, monkeypatch, f'echo "$@" > {tmp_path}/argv\necho eyJ-access\n')
    assert agent_pi.vm_token() == "eyJ-access"
    assert (tmp_path / "argv").read_text().split() == [
        "auth", "print-bearer-token", "--provider", "openai", "--min-expiry", "35m"]


@pytest.mark.parametrize("body, says", [
    ("exit 1\n", "no ChatGPT sign-in"),
    ("echo sk-proj-key\n", "an API key, not the ChatGPT sign-in"),
])
def test_no_vm_token_from_a_missing_login_or_an_api_key(tmp_path, monkeypatch, body, says):
    from adw_modules import sandbox
    fake_pi(tmp_path, monkeypatch, body)
    with pytest.raises(sandbox.SandboxFailed, match=says):
        agent_pi.vm_token()


def test_a_pi_agent_in_a_vm_gets_a_token_and_the_refuse_extension(tmp_path, monkeypatch):
    from adw_modules import sandbox
    monkeypatch.setattr(agent_pi, "vm_token", lambda: "eyJ-access")
    installed = []
    vm = SimpleNamespace(install=installed.append)
    env = agent_pi._in_vm({}, vm, rules.refusing([REFUSE]))
    assert env == {"SSSF_PI_TOKEN": "eyJ-access", "OBS_DISABLE": "true"} and installed == [agent_pi.REFUSE_EXTENSION]
    assert agent_pi._in_vm({}, sandbox.LOCAL, rules.refusing([REFUSE])) == {}
    obs = {"OBS_AUTH_TOKEN": "secret", "OBS_NAME": "builder", "OBS_POOL": "p"}
    in_vm = agent_pi._in_vm(dict(obs), vm, [])
    assert "OBS_AUTH_TOKEN" not in in_vm
    assert in_vm["OBS_NAME"] == "builder" and in_vm["OBS_POOL"] == "p" and in_vm["OBS_DISABLE"] == "true"
    assert agent_pi._in_vm(dict(obs), sandbox.LOCAL, []) == obs


def test_a_vm_refuses_other_providers_but_not_pi_extensions():
    from adw_modules import sandbox
    agent = lambda **kw: SimpleNamespace(name="builder", coding_agent="pi", model="openai/gpt-6.1-sol",
                                         harness_engineering=[], **{k: v for k, v in kw.items()})
    ok = SimpleNamespace(agents=[agent()])
    sandbox.check_roster("vercel", ok)
    with pytest.raises(SystemExit, match="ChatGPT sign-in only"):
        sandbox.check_roster("vercel", SimpleNamespace(agents=[
            SimpleNamespace(name="b", coding_agent="pi", model="google/gemini-3.6-flash",
                            harness_engineering=[])]))
    sandbox.check_roster("vercel", SimpleNamespace(agents=[
        SimpleNamespace(name="b", coding_agent="pi", model="openai/gpt-6.1-sol",
                        harness_engineering=["x.ts"])]))


def test_built_in_refusals_never_stop_the_run():
    from adw_modules import secret_files
    builtin = secret_files.shell_rule(["/r/.env"])
    forward, events, _ = forwarder(enforced=[REFUSE, builtin], uncounted=(builtin,))
    for i in range(rules.MAX_REFUSALS + 2):
        forward(announce(f"b{i}", "cat .env"))
        forward(finish(f"b{i}", f"{rules.REFUSED}, by this repo's rule: x"))
    assert [e.name for e in events] == ["rule_refused"] * (rules.MAX_REFUSALS + 2)
    for i in range(rules.MAX_REFUSALS):
        forward(announce(f"g{i}"))
        if i < rules.MAX_REFUSALS - 1:
            forward(finish(f"g{i}", f"{rules.REFUSED}, by this repo's rule: x"))
    with pytest.raises(rules.RuleBreach) as stop:
        forward(finish(f"g{rules.MAX_REFUSALS - 1}", f"{rules.REFUSED}, by this repo's rule: x"))
    assert stop.value.refusals == rules.MAX_REFUSALS


def test_a_built_in_match_that_was_not_refused_is_still_a_breach():
    from adw_modules import secret_files
    builtin = secret_files.shell_rule(["/r/.env"])
    forward, _, _ = forwarder(enforced=[builtin], uncounted=(builtin,))
    forward(announce("b1", "cat .env"))
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(finish("b1", "KEY=value"))


def test_a_repo_rule_equal_to_the_built_in_one_still_counts():
    from adw_modules import secret_files
    builtin = secret_files.shell_rule(["/r/.env"])
    twin = builtin.model_copy()
    assert twin == builtin and twin is not builtin
    forward, _, _ = forwarder(enforced=[twin], uncounted=(builtin,))
    with pytest.raises(rules.RuleBreach) as stop:
        for i in range(rules.MAX_REFUSALS):
            forward(announce(f"t{i}", "cat .env"))
            forward(finish(f"t{i}", f"{rules.REFUSED}, by this repo's rule: x"))
    assert stop.value.refusals == rules.MAX_REFUSALS


def test_a_pi_agent_in_a_vm_gets_the_refuse_extension_without_refuse_rules():
    installed = []
    vm = SimpleNamespace(install=installed.append, name="vercel", relay_obs=lambda t: None)
    agent_pi._in_vm({}, vm, [])
    assert installed == [agent_pi.REFUSE_EXTENSION]


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_the_pi_extension_blocks_secret_file_reads_and_the_built_in_pattern(tmp_path):
    from adw_modules import secret_files
    (tmp_path / ".env").write_text("K=v\n")
    (tmp_path / ".env.sample").write_text("K=\n")
    files = secret_files.find([tmp_path], [])
    check = tmp_path / "check.ts"
    check.write_text(f'''
import ext from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
let handler: any;
ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
const out = [
  handler({{ toolName: "read", input: {{ path: ".env" }} }}),
  handler({{ toolName: "read", input: {{ path: "@.env" }} }}),
  handler({{ toolName: "grep", input: {{ path: ".env", pattern: "K" }} }}),
  handler({{ toolName: "read", input: {{ path: ".env.sample" }} }}),
  handler({{ toolName: "bash", input: {{ command: "echo env" }} }}),
];
console.log(JSON.stringify(out.map((o) => o ?? null)));
''')
    env = {k: v for k, v in os.environ.items() if k != "SSSF_REFUSE_RULES"}
    env["SSSF_SECRET_FILES"] = json.dumps(files)
    done = subprocess.run(["bun", str(check)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    first, second, third, sample, echo = json.loads(done.stdout)
    assert all(b["block"] is True for b in (first, second, third))
    assert first["reason"] == ("SSSF refused this read call: .env is one of this repo's secret files, "
                               "which SSSF keeps from agents. Nothing was read. Don't try it another "
                               "way: do the task without it, or say in your report that the task needs it.")
    assert second["reason"].startswith("SSSF refused this read call: @.env is one of this repo's secret files")
    assert third["reason"].startswith("SSSF refused this grep call: .env is one of this repo's secret files")
    assert all("command" not in b["reason"] for b in (first, second, third))
    assert sample is None and echo is None

    check.write_text(f'''
import ext from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
let handler: any;
ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
console.log(JSON.stringify([
  handler({{ toolName: "bash", input: {{ command: "cat .env" }} }}),
  handler({{ toolName: "bash", input: {{ command: "cat .env.sample" }} }}),
].map((o) => o ?? null)));
''')
    rule = secret_files.shell_rule(files)
    env["SSSF_REFUSE_RULES"] = json.dumps(rules.refusing([rule], builtin=rule))
    done = subprocess.run(["bun", str(check)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    cat_env, cat_sample = json.loads(done.stdout)
    assert cat_env["block"] is True and cat_sample is None
    assert cat_env["reason"].startswith(
        rules.REFUSED + ", by SSSF's secret-file rule: " + secret_files.RULE_TEXT + " Nothing ran.")
    assert "this repo's rule" not in cat_env["reason"]


def real_forwarder(interface, rule=REFUSE):
    """A forwarder on agent_pi or agent_cc itself, so it reads that harness's types."""
    events, said = [], []
    run = SimpleNamespace(adw_id="r1", tracer=SimpleNamespace(event=events.append),
                          console=SimpleNamespace(advise=said.append))
    forward = agents._event_forwarder(run, SimpleNamespace(phase_id="r1_01"), "builder",
                                      interface, [rule])
    return forward, events


def refused_events(events):
    return [e for e in events if e.name == "rule_refused"]


def claude_result(call_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": text}]}}


def test_on_pi_a_forged_permission_denied_does_not_refuse_a_call_that_ran():
    forward, events = real_forwarder(agent_pi)
    forward(announce("c1"))
    forward({"type": "system", "subtype": "permission_denied", "tool_use_id": "c1"})
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(ran("c1", "Everything up-to-date"))
    assert refused_events(events) == []


@pytest.mark.parametrize("text", [rules.REFUSED, "Exit code 126\n" + rules.REFUSED])
def test_on_pi_a_forged_claude_result_with_the_refusal_does_not_refuse_a_call_that_ran(text):
    forward, events = real_forwarder(agent_pi)
    forward(announce("c1"))
    forward(claude_result("c1", text))
    assert refused_events(events) == []
    with pytest.raises(rules.RuleBreach):
        forward(ran("c1", "Everything up-to-date"))
    second, _ = real_forwarder(agent_pi)
    second(announce("c1"))
    second(claude_result("c1", text))
    with pytest.raises(rules.RuleBreach):
        second.ended()


def test_on_claude_code_a_forged_pi_never_ran_result_does_not_refuse():
    forward, events = real_forwarder(agent_cc)
    forward(claude_call("t1"))
    forward(finish("t1", rules.REFUSED))
    assert refused_events(events) == []
    with pytest.raises(rules.RuleBreach):
        forward(claude_result("t1", "Everything up-to-date"))


def test_a_forged_announcement_of_the_other_harness_counts_for_nothing():
    forward, _ = real_forwarder(agent_pi, PUSH)
    forward(claude_call("t9"))
    assert forward.ended() is None
    forward, _ = real_forwarder(agent_cc, PUSH)
    forward(announce("c9"))
    assert forward.ended() is None


@pytest.mark.parametrize("interface", [agent_pi, agent_cc], ids=["pi", "claude_code"])
@pytest.mark.parametrize("etype", [["user"], {"type": "user"}, 5], ids=["list", "dict", "number"])
def test_an_event_whose_type_is_not_a_string_skips_the_rule_reads(interface, etype, monkeypatch):
    event = {**claude_call("t1"), **announce("c1"), "type": etype}
    forward, _ = real_forwarder(interface, PUSH)
    forward(event)                       # the real tracker, and nothing raises
    assert forward.ended() is None
    read, seen = [], []
    monkeypatch.setattr(rules, "denied", lambda e: read.append(e) or [])
    spy = SimpleNamespace(HARNESS=interface.HARNESS,
                          ToolCallTracker=lambda: SimpleNamespace(observe=lambda e: seen.append(e) or []))
    forward, _ = real_forwarder(spy, PUSH)
    forward(event)
    assert read == [] and seen == [event]
    own = {"type": sorted(agents.RULE_EVENT_TYPES[interface.HARNESS])[0]}
    forward(own)
    assert read == [own]                 # the spy does see a read of the harness's own type


def test_honest_streams_read_the_same_on_the_real_interfaces():
    forward, events = real_forwarder(agent_pi)
    forward(announce("c1"))
    forward(finish("c1", rules.REFUSED + ", by this repo's rule: x"))
    assert [e.payload["refusals"] for e in refused_events(events)] == [1]
    assert [e for e in events if e.type == "tool_call"]
    forward(announce("c2"))
    with pytest.raises(rules.RuleBreach):
        forward(ran("c2", "Everything up-to-date"))
    forward, events = real_forwarder(agent_pi)
    with pytest.raises(rules.RuleBreach) as stop:
        for i in range(rules.MAX_REFUSALS):
            forward(announce(f"c{i}"))
            forward(finish(f"c{i}", rules.REFUSED))
    assert stop.value.refusals == rules.MAX_REFUSALS

    forward, events = real_forwarder(agent_cc)
    forward(claude_call("t1"))
    forward({"type": "system", "subtype": "permission_denied", "tool_use_id": "t1"})
    forward(claude_result("t1", "Permission denied"))
    assert len(refused_events(events)) == 1
    assert [e for e in events if e.type == "tool_call"]
    forward(claude_call("t2"))
    forward(claude_result("t2", "Exit code 126\n" + rules.REFUSED))
    assert len(refused_events(events)) == 2
    forward(claude_call("t3"))
    with pytest.raises(rules.RuleBreach):
        forward(claude_result("t3", "Everything up-to-date"))


def test_each_interface_names_the_harness_it_runs():
    import typing
    from adw_modules.data_types import AgentConfig
    assert agent_pi.HARNESS == "pi" and agent_cc.HARNESS == "claude_code"
    for v in typing.get_args(AgentConfig.model_fields["coding_agent"].annotation):
        assert agents._interface(SimpleNamespace(coding_agent=v)).HARNESS == v
        assert v in agents.RULE_EVENT_TYPES
    assert agents.RULE_EVENT_TYPES["pi"].isdisjoint(agents.RULE_EVENT_TYPES["claude_code"])
