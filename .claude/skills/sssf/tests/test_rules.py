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
from pydantic import ValidationError

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agent_pi, agents, repo_settings, rules
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


def forwarder(can_refuse=True, rule=REFUSE):
    events, said = [], []
    run = SimpleNamespace(adw_id="r1", tracer=SimpleNamespace(event=events.append),
                          console=SimpleNamespace(advise=said.append))
    interface = SimpleNamespace(ToolCallTracker=lambda: SimpleNamespace(observe=lambda e: []))
    forward = agents._event_forwarder(run, SimpleNamespace(phase_id="r1_01"), "builder",
                                      interface, [rule], can_refuse=can_refuse)
    return forward, events, said


def announce(call_id, command="git push"):
    return {"type": "tool_execution_start", "toolCallId": call_id, "toolName": "bash",
            "args": {"command": command}}


def finish(call_id, text):
    return {"type": "tool_execution_end", "toolCallId": call_id, "toolName": "bash",
            "result": {"content": [{"type": "text", "text": text}]}, "isError": True}


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


def test_pi_loads_the_refusing_extension_only_for_refuse_rules(tmp_path, monkeypatch):
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
        assert (str(agent_pi.REFUSE_EXTENSION) in argv) is expect
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
    assert spared is None and other is None


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
