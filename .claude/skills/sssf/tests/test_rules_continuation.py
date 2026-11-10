"""All three matchers see through a backslash-newline.

A shell drops a backslash followed by a newline before it reads a command, so
`no\\<newline>such-sssf-fixture test live` runs `nosuch-sssf-fixture test live`.
rules.breach, sssf_refuse.py and pi's extension (sssf_rules.ts) also search the
command with those removed.

The rows run a stub named `nosuch-sssf-fixture` in place of the `just` live-test
command, because the hub's agentsh policy refuses that command in every process
and counts each refusal toward the stop, so a builder that ran these tests under
agentsh would be stopped. No real program has that name, nor either fragment a
split leaves (`no`, `such-sssf-fixture`).

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, rules, secret_files
from test_rules import announce, claude_line, forwarder, ran, starter_push_rule
from test_rules_multiline import _bun_handler, _pi_block

NL = "\n"
BS = "\\"
FIXTURE = "nosuch-sssf-fixture"
LIVE = rules.AgentRule(rule="no live tests", forbid=[r"\bnosuch-sssf-fixture\s+test\s+live\b"], on_match="refuse")
PI = rules.AgentRule(rule="no nested pi", forbid=[r"(^|[;&|]\s*)pi\b"], on_match="refuse")
END = rules.AgentRule(rule="no live tests at the end", forbid=[r"\bnosuch-sssf-fixture\s+test\s+live$"], on_match="refuse")
RULES = {"LIVE": LIVE, "PI": PI, "END": END}

FIXTURE_RUN, PIRUN, NONE = "nosuch-sssf-fixture test live", "pi -p hi", None

# (rule, command, matches, what real bash runs)
ROWS = [
    ("LIVE", f"no{BS}{NL}such-sssf-fixture test live", True, FIXTURE_RUN),
    ("LIVE", f"nosuch-sssf-fixture test {BS}{NL}live", True, FIXTURE_RUN),
    ("LIVE", f"nosuch-sssf-fixture {BS}{NL}test {BS}{NL}live", True, FIXTURE_RUN),
    ("LIVE", f"{BS}{NL}nosuch-sssf-fixture test live", True, FIXTURE_RUN),
    ("LIVE", f"no{BS}{NL}{BS}{NL}such-sssf-fixture test live", True, FIXTURE_RUN),
    ("LIVE", f"echo hi{NL}no{BS}{NL}such-sssf-fixture test live", True, FIXTURE_RUN),
    ("LIVE", f"true; {BS}{NL}no{BS}{NL}such-sssf-fixture test live", True, FIXTURE_RUN),
    ("LIVE", f"a=1 {BS}{NL}no{BS}{NL}such-sssf-fixture test live", True, FIXTURE_RUN),
    ("LIVE", f"echo $(no{BS}{NL}such-sssf-fixture test live)", True, FIXTURE_RUN),
    ("LIVE", f"bash -c 'no{BS}{NL}such-sssf-fixture test live'", True, FIXTURE_RUN),
    ("LIVE", f"bash -c 'nosuch-sssf-fixture test {BS}{NL}live'", True, FIXTURE_RUN),
    ("LIVE", f"cat <<'EOF' | bash{NL}no{BS}{NL}such-sssf-fixture test live{NL}EOF", True, FIXTURE_RUN),
    ("LIVE", f"bash <<'EOF'{NL}nosuch-sssf-fixture test {BS}{NL}live{NL}EOF", True, FIXTURE_RUN),
    ("LIVE", f"eval 'no{BS}{NL}such-sssf-fixture test live'", True, FIXTURE_RUN),
    ("LIVE", f'bash -c "no{BS}{BS}{NL}such-sssf-fixture test live"', True, FIXTURE_RUN),
    ("LIVE", f"bash <<EOF{NL}no{BS}{BS}{NL}such-sssf-fixture test live{NL}EOF", True, FIXTURE_RUN),
    ("LIVE", "nosuch-sssf-fixture test live", True, FIXTURE_RUN),
    ("PI", f"p{BS}{NL}i -p hi", True, PIRUN),
    ("PI", f"echo hi{NL}p{BS}{NL}i -p hi", True, PIRUN),
    ("PI", f"true; p{BS}{NL}i -p hi", True, PIRUN),
    ("PI", f"echo hi {BS}{NL}pi -p hi", True, NONE),
    ("END", f"nosuch-sssf-fixture test live{BS}{NL}", True, FIXTURE_RUN),
    ("LIVE", f"nosuch-sssf-fixture test{BS}{NL}live", False, "nosuch-sssf-fixture testlive"),
    ("LIVE", "nosuch-sssf-fixture test unit", False, "nosuch-sssf-fixture test unit"),
    ("PI", f"echo pi{BS}{NL}p install x", False, NONE),
    ("LIVE", f"no{BS}\r{NL}such-sssf-fixture test live", False, NONE),
    # Known limits: a flip here means a limit was closed. Two are quotes
    # inside a word, five are a line the shell doesn't continue followed by a
    # split command (the removal joins what the shell keeps, so the line before
    # swallows the command's start).
    ("LIVE", f"n{BS}osuch-sssf-fixture test live", False, FIXTURE_RUN),
    ("LIVE", "n''osuch-sssf-fixture test live", False, FIXTURE_RUN),
    ("LIVE", f"x{BS}{BS}{NL}no{BS}{NL}such-sssf-fixture test live", False, FIXTURE_RUN),
    ("LIVE", f"# note{BS}{NL}no{BS}{NL}such-sssf-fixture test live", False, FIXTURE_RUN),
    ("LIVE", f"# note{BS}{NL}nosuch-sssf-fixture test {BS}{NL}live", False, FIXTURE_RUN),
    ("PI", f"true {BS}{BS}{NL}p{BS}{NL}i -p hi", False, PIRUN),
    ("PI", f"# note {BS}{NL}p{BS}{NL}i -p hi", False, PIRUN),
]
IDS = [f"{i}-{row[0]}" for i, row in enumerate(ROWS)]
needs_bun = pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")


def stubs(tmp_path):
    """A directory of recording `just` and `pi` stubs, and the log they write."""
    bin_dir, log = tmp_path / "stubs", tmp_path / "log"
    bin_dir.mkdir(exist_ok=True)
    for name in (FIXTURE, "just", "pi"):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        stub.chmod(0o755)
    return {**{k: v for k, v in os.environ.items() if k not in ("SSSF_NEXT_SHELL_PREFIX", "SSSF_REFUSE_RULES")},
            "PATH": f"{bin_dir}:/usr/bin:/bin"}, log


def run_prefix(tmp_path, line, rule_list, builtin=None):
    env, log = stubs(tmp_path)
    env["SSSF_REFUSE_RULES"] = json.dumps(rules.refusing(rule_list, builtin))
    return subprocess.run([sys.executable, str(agent_cc.REFUSE_SHELL), line], capture_output=True,
                          text=True, env=env, cwd=tmp_path)


@pytest.mark.parametrize("name, command, expected, _runs", ROWS, ids=IDS)
def test_breach_reads_through_a_backslash_newline(name, command, expected, _runs):
    assert (rules.breach([RULES[name]], command) is not None) is expected


@pytest.mark.parametrize("name, command, expected, _runs", ROWS, ids=IDS)
def test_the_prefix_reads_through_a_backslash_newline(tmp_path, name, command, expected, _runs):
    done = run_prefix(tmp_path, claude_line(command), [RULES[name]])
    if expected:
        assert done.returncode == 126 and done.stderr.startswith(rules.REFUSED)
    else:
        assert done.returncode != 126 and rules.REFUSED not in done.stderr


@needs_bun
@pytest.mark.parametrize("name", list(RULES))
def test_pis_handler_reads_through_a_backslash_newline(tmp_path, name):
    rows = [row for row in ROWS if row[0] == name]
    blocks = _bun_handler(tmp_path, json.dumps(rules.refusing([RULES[name]])), [row[1] for row in rows])
    assert [bool((b or {}).get("block")) for b in blocks] == [row[2] for row in rows]


@pytest.mark.parametrize("name, command, _expected, runs", ROWS, ids=IDS)
def test_the_table_is_honest_about_what_bash_runs(tmp_path, name, command, _expected, runs):
    env, log = stubs(tmp_path)
    subprocess.run(["bash", "-c", command], env=env, cwd=tmp_path, capture_output=True, text=True)
    recorded = log.read_text().splitlines() if log.exists() else []
    assert recorded == ([] if runs is None else [runs])


@pytest.mark.parametrize("command, name", [
    (f"true && eval no{BS}{NL}such-sssf-fixture test live < /dev/null && pwd -P", "LIVE"),
    (f"no{BS}{NL}such-sssf-fixture test live", "LIVE"),
    (f"x; eval {BS}{NL}'pi -p hi'", "PI"),
    (f"x; eval 'p{BS}{NL}i -p hi'", "PI"),
])
def test_the_prefix_refuses_lines_of_other_shapes(tmp_path, command, name):
    done = run_prefix(tmp_path, command, [RULES[name]])
    assert done.returncode == 126 and done.stderr.startswith(rules.REFUSED)


def test_the_prefix_misses_quotes_inside_a_word_across_the_wrapper(tmp_path):
    # A known limit (quotes inside a word); no real wrapper produces this line.
    line = f"true && eval 'no{BS}'\"{NL}\"'such-sssf-fixture test live' < /dev/null && pwd -P"
    done = run_prefix(tmp_path, line, [LIVE])
    assert done.returncode != 126 and rules.REFUSED not in done.stderr


# ── agreement ────────────────────────────────────────────────────────────────

RULE_A = rules.AgentRule(rule="rule A", forbid=[r"\bnosuch-sssf-fixture\s+test\s+live\b"], on_match="refuse")
RULE_B = rules.AgentRule(rule="rule B", forbid=[r"\bsuch-sssf-fixture\s+test\s+live\b"], on_match="refuse")


@needs_bun
def test_all_three_name_the_first_rule_when_only_a_later_one_matches_as_written(tmp_path):
    command = f"no{BS}{NL}such-sssf-fixture test live"
    assert not re.search(RULE_A.forbid[0], command) and re.search(RULE_B.forbid[0], command)
    found, _ = rules.breach([RULE_A, RULE_B], command)
    assert found is RULE_A
    done = run_prefix(tmp_path, claude_line(command), [RULE_A, RULE_B])
    assert "rule A" in done.stderr and "rule B" not in done.stderr
    [block] = _bun_handler(tmp_path, json.dumps(rules.refusing([RULE_A, RULE_B])), [command])
    assert "rule A" in block["reason"] and "rule B" not in block["reason"]


def claude_events(call_id, command, result):
    return [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": result}]}}]


@needs_bun
def test_a_split_push_is_a_counted_refusal_on_both_harnesses_and_a_breach_when_it_ran(tmp_path):
    rule = starter_push_rule()
    command = f"git {BS}{NL}push origin main"
    found = rules.breach([rule], command)
    assert found is not None
    [block] = _bun_handler(tmp_path, json.dumps(rules.refusing([rule])), [command])
    assert block["block"] is True
    forward, events, _ = forwarder(rule=rule)
    forward(announce("c1", command))
    forward(_pi_block("c1", block["reason"]))
    assert [e.name for e in events] == ["rule_refused"]

    done = run_prefix(tmp_path, claude_line(command), [rule])
    assert done.returncode == 126
    forward, events, _ = forwarder(rule=rule)
    for event in claude_events("t1", command, f"Exit code 126\n{done.stderr}"):
        forward(event)
    assert [e.name for e in events] == ["rule_refused"]

    forward, events, _ = forwarder(rule=rule)
    forward(announce("c2", command))
    with pytest.raises(rules.RuleBreach, match="may have run") as stop:
        forward(ran("c2", "done"))
    assert stop.value.pattern == found[1]
    assert json.dumps(found[1]) in done.stderr


@needs_bun
def test_the_built_in_secret_rule_sees_through_a_backslash_newline(tmp_path):
    builtin = secret_files.shell_rule(["/r/.env"])
    command = f"cat .e{BS}{NL}nv"
    assert rules.breach([builtin], command) is not None
    done = run_prefix(tmp_path, claude_line(command), [builtin], builtin)
    assert done.returncode == 126 and "secret-file rule" in done.stderr
    [block] = _bun_handler(tmp_path, json.dumps(rules.refusing([builtin], builtin)), [command])
    assert block["block"] is True


def test_a_stop_rule_stops_the_run_on_a_split_command():
    stop = rules.AgentRule(rule="no live tests", forbid=[r"\bnosuch-sssf-fixture\s+test\s+live\b"], on_match="stop")
    forward, events, _ = forwarder(rule=stop, can_refuse=False)
    with pytest.raises(rules.RuleBreach):
        forward(announce("c1", f"no{BS}{NL}such-sssf-fixture test live"))


def test_a_long_run_of_backslashes_is_searched_in_linear_time():
    start = time.monotonic()
    assert rules.breach([LIVE], BS * 200_000) is None
    assert time.monotonic() - start < 5
