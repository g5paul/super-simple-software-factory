# Plan: count Claude Code's own denial of a call as a refusal

## Summary

Claude Code applies SSSF's `--disallowedTools "Read(//<abs>)"` secret-file
denies to Bash too. In live run ee920adc it denied Bash `cat .env` before
`sssf_refuse.py` ran. It emitted a `system`/`permission_denied` event for that
`tool_use_id`, then a `tool_result` that is not SSSF's refusal text. The built-in
secret refuse rule also matches `cat .env`. So `agents._event_forwarder` held the
call as pending, read the result as "it ran", and raised `RuleBreach`. Nothing
ran, but the run stopped.

The fix: the forwarder records the `tool_use_id` of every `permission_denied`
event. When a pending refuse-rule call's result arrives, the call counts as
refused if its result is SSSF's refusal **or** its id was denied. Everything
after that check stays as it is: the `uncounted` exemption, the `rule_refused`
event, the console line and the `MAX_REFUSALS` stop. Stop-mode `forbid` rules
are untouched.

**Event order, checked against the live raw output.** In
`~/github/sssf-sandbox/adws/adw_data/sessions/{ee920adc,34fb3c8c,99d32ca5}/builder/raw_output.jsonl`,
each `permission_denied` event comes on the line right after the call's
`tool_use`, before any `tool_result`. The design relies on that order. If a
denial ever arrived after its result, the call would raise `RuleBreach`, which is
what the baseline does: it fails closed. To be safe in the other direction, the
recorded id set is checked when the result arrives, so it does not matter
whether the denial comes before or after the `tool_use`.

## Baseline

`main` at **`635ef9c`** ("Update SSSF to skill a3c2536: keep secret files and
.env keys from agents"). Every finding is judged against that commit.

This branch (`guard-cc-denied`) also holds 6ef1833 (run 05259b95): agent_cc no
longer crashes on a `permission_denied` event whose `message` is a string
(`agent_cc._message`). That work stays. This change builds on it.

## Threat model

- The agent runs as the engineer's own user, with the engineer's shell, files
  and credentials. A guard at SSSF's level reads the harness's event stream and
  shapes the harness's flags and environment. It cannot stop an agent that is set
  on reading a file the user can read: an obfuscated name (`cat .e''nv`), a path
  built at runtime, `/proc/<pid>/environ`, or a hard link. The bar is
  accidental exposure. VMs are the boundary for `.env`.
- The stream SSSF reads is Claude Code's own stdout. An agent's tool output reaches it
  only as text inside a `tool_result` block, never as a top-level event. So an
  agent cannot forge a `permission_denied` event through a command's output.
  `rules.denied` reads only top-level `type: "system"` events, never block content.
- After this change SSSF trusts Claude Code's claim that it denied a call. If
  Claude Code emitted `permission_denied` for a call and then ran it anyway (a
  Claude Code bug), SSSF would count a run command as refused. The baseline
  would have stopped the run there. That trust is what the request asks for, and
  it is matched by `tool_use_id` only, never by message text. A denial for one
  call never excuses another.
- What the agent can change is still enforced after the fact by `writes` /
  `protected_files` (`permissions.py`), which this change does not touch.

## Bar

Against `635ef9c`, the change must:

1. Count a refuse-rule match as refused, never as run, when Claude Code emitted
   `permission_denied` with that call's `tool_use_id`. That holds whatever the
   result text is. The `rule_refused` event and console line are the same as for
   an SSSF refusal. The refusal counts toward `MAX_REFUSALS` exactly as an SSSF
   refusal of the same rule does: a repo refuse rule's refusals count, and the
   built-in secret-file rule stays in `uncounted` (config.md: "Refusals by the
   built-in rule never count toward the three-refusal stop").
2. Judge a call with no `permission_denied` event exactly as today. A refuse-rule
   match whose result is neither SSSF's refusal nor denied by its own id still
   raises `RuleBreach` ("may have run").
3. Keep a stop-mode `forbid` match (no `on_match: refuse`, or a harness that
   can't refuse) raising `RuleBreach` at the announcement, whether or not
   Claude Code denies the call.

It must never, where the baseline does otherwise:

- keep an agent file, run agent code, or delete or overwrite an engineer file;
- match a denial by message text, or let a denial for one id excuse another;
- change pi's behaviour: pi emits no `permission_denied` event.

## Files to change

All guard code is the skill's template copy. The installed copy under
`adws/adw_modules/` is protected (see `## For the engineer`).

### 1. `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

- Add, right after `results()`:

  ```python
  def denied(event: dict) -> list[str]:
      """The call id in a Claude Code `permission_denied` system event: Claude
      Code denied that call itself, by its own permission rules, before it ran
      (claude 2.1.319 sends it right after the call's tool_use). Matched by id
      only; the event's `message` is never read."""
      if event.get("type") != "system" or event.get("subtype") != "permission_denied":
          return []
      call_id = event.get("tool_use_id")
      return [call_id] if isinstance(call_id, str) and call_id else []
  ```

- Module docstring, last sentence ("The stream check stays as the backstop: a
  matching call whose result isn't that refusal ran, and is a breach."). Change
  it to say that a matching call whose result isn't that refusal, and that Claude
  Code didn't deny itself (a `permission_denied` event for its id), ran and is a
  breach.

Leave `refused()`, `results()` and `calls()` unchanged.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py` — `_event_forwarder`

- Add state next to `pending`:
  `denied: set[str] = set()  # call ids Claude Code denied itself (permission_denied)`
- At the top of `forward`, before the `rules.calls` loop:
  `denied.update(rules.denied(event))`
- In the `rules.results` loop, after `rule, pattern, command = pending.pop(call_id)`:

  ```python
  by_claude = call_id in denied
  denied.discard(call_id)
  if not (by_claude or rules.refused(text)):
      raise rules.RuleBreach(agent_name, rule, pattern, command)
  ```

  Everything below it stays byte-for-byte: the `uncounted` check, the
  `rule_refused` event and its payload, `console.advise` and the
  `MAX_REFUSALS` raise. Also call `denied.discard(call_id)` for a result whose
  id is not pending (the `continue` branch), so the set doesn't grow with
  denied Read/Grep calls.
- Do **not** consult `denied` in the `rules.calls` loop. The stop branch
  (`elif found: raise`) and the `can_refuse=False` path stay as they are.
- Docstring: in the paragraph "A refuse rule waits for the call's result
  instead…", add that a call Claude Code denied itself (a `permission_denied`
  event with its `tool_use_id`, `rules.denied`) also means nothing ran, and that
  it is counted like SSSF's refusal. Change the last sentence, "A call by such a
  rule whose result isn't the refusal still raises RuleBreach", to "…isn't the
  refusal, and that Claude Code didn't deny, still raises RuleBreach."

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` — module docstring only

Replace the lines added by 6ef1833 ("Each denial also arrives as a
`permission_denied` system event whose `message` is a string. SSSF ignores it,
and the trace records the denied call from its `tool_result`.") with this: Claude
Code applies those denies to any tool that reads the file, Bash included, so it
may deny `cat .env` before sssf_refuse.py runs. Each denial arrives as a
`permission_denied` system event, whose `message` is a string. The forwarder
(agents._event_forwarder, via rules.denied) counts that call as refused, by its
`tool_use_id`, and the trace records it from its `tool_result`. Also change
"denied to Read and Grep" on line 21 to "denied to Read and Grep (and to Bash
reads, by Claude Code itself)". No code change in this file.

### 4. `.claude/skills/sssf/references/config.md`

- Secret files section, the **Claude Code** bullet (line ~417). Replace "Read and
  Grep refuse it. Claude Code also emits a `permission_denied` system event for
  each refusal; SSSF ignores it, the agent reads the refusal and goes on, and the
  trace records the call as a failed tool call." with wording that says all of this:
  - Claude Code may deny a protected-file read itself, in any tool: Read, Grep,
    and Bash `cat .env` too, before SSSF's shell prefix sees it.
  - Each denial arrives as a `permission_denied` system event naming the call.
    SSSF counts that call as refused, as if its own refusal were the result,
    matching by the call's id, never the message.
  - The agent reads the denial and goes on. A Bash call a refuse rule matched
    records `rule_refused`. The trace records the call as a failed tool call.
- The paragraph after the bullets (line ~421): change "a matching call that
  wasn't refused stops the run" to "a matching call that neither SSSF nor
  Claude Code refused stops the run".
- The `on_match: refuse` / Claude Code paragraph (line ~397, "Claude Code
  agents run with `--safe-mode`…"): add one sentence at its end. It says that a
  matching `Bash` call Claude Code denies itself, by its own permission rules
  (its `permission_denied` event), counts as refused the same way, three-refusal
  stop included, and that a `forbid` rule without `on_match: refuse` still stops
  the run on a match, denied or not.

### 5. `.claude/skills/sssf/templates/repo.yaml` — comment only

Lines 71–73: change "Claude Code's Read and Grep and pi's read, grep, find and ls
refuse them, and so does a shell command that names one." to say that Claude
Code refuses them in Read, Grep and Bash, pi's read, grep, find and ls refuse
them, and so does a shell command that names one. Keep "Those refusals never
stop the run." Do not change `agent_rules: []` or any other value.

### 6. Tests — `.claude/skills/sssf/tests/test_agent_cc.py` and `.claude/skills/sssf/tests/test_rules.py`

Write the tests first and run them once. The replay tests 1, 2a and 3 must fail
on the current code (1 and 2a with `RuleBreach … may have run`). 2b passes
before the change only if its result is SSSF's refusal, so build 2b's stream
with the denial-text result too. Then make the change.

The settings come from the shipped `.claude/skills/sssf/templates/repo.yaml`, the
same way `tests/test_secret_files.py::shipped_settings` does, with
`agent_rules` overridden where a test needs a repo rule. The forwarder reads no
roster, so no `sssf.config.yaml` is loaded, and no test-only config file is
written.

In `test_agent_cc.py`, add a section `# ── Claude Code's own denials ──` after
the `# ── refuse rules ──` section. Add helpers:

- `_guarded(tmp_path, agent_rules=())`:
  - Write `tmp_path / ".env"` (`APP_CANARY=x\n`).
  - Build `RepoSettings` from the shipped `templates/repo.yaml` with
    `agent_rules` overridden.
  - Set `files = secret_files.find([tmp_path], settings.secret_files)` and
    `builtin = secret_files.shell_rule(files)`.
  - Set `enforced = [*settings.agent_rules, builtin]` (the order `agents.py`
    uses).
  - Build a fake `run`: `SimpleNamespace(adw_id="r1",
    tracer=SimpleNamespace(event=events.append),
    console=SimpleNamespace(advise=said.append))`.
  - Create the forwarder with `agents._event_forwarder(run,
    SimpleNamespace(phase_id="r1_01"), "builder", agent_cc, enforced,
    uncounted=(builtin,))`.
  - Return `(forward, events, builtin)`.
- `_denied_bash(call_id, command="cat .env", result=None)` returns these
  events:
  1. An `assistant` event. Its `message` has a unique `id`,
     `model: "claude-haiku-5-5"`, `role: "assistant"`, the `usage` from
     cc_turn1's first assistant event (as
     `test_a_denied_read_is_a_failed_call_and_the_call_goes_on` gets it), and one
     `tool_use` block: `name: "Bash"`, `id: call_id`,
     `input: {"command": command, "description": "Read .env file"}`.
  2. The `permission_denied` event from the request, with `tool_use_id`
     set to `call_id`:
     `{"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": <call_id>, "decision_reason_type": "rule", "message": "Permission to use Bash with command cat .env has been denied."}`
  3. A `user` event with one `tool_result` for `call_id`, `is_error: True`. Its
     `content` is `result`, or `"Permission to use Bash with command cat .env has
     been denied."` when `result` is None. That text is not `rules.REFUSED`, so
     the test exercises the new path.

  Add `_refused_bash(call_id)` as the same events without the
  `permission_denied` event, with the result
  `"Exit code 126\n" + rules.REFUSED + ", by this repo's rule: …"`. It gives the
  SSSF-refusal comparison.

Use `_stream(tmp_path, name, middle)`, which already exists, to wrap events in
cc_turn1's init and result. Feed them through `agent_cc.run(request(tmp_path),
on_event=forward)` with the `fake` fixture.

Tests:

1. `test_a_bash_read_claude_code_denies_is_a_refusal_and_the_call_goes_on`
   - Stream `_denied_bash("toolu_011E3m58bgpqe7LGELGbwmZ2")` through agent_cc
     with only the built-in rule.
   - `agent_cc.run` returns, and `outcome.text == result["result"]`.
   - No `RuleBreach`.
   - Exactly one `rule_refused` event, with payload `command == "cat .env"`,
     `rule == secret_files.RULE_TEXT`, and `refusals == 0` (the built-in rule is
     uncounted, as for an SSSF refusal).
   - A `tool_call` event for the Bash call, with `ok` False.
   - Run the same with `_refused_bash` and assert that the `rule_refused`
     payloads are equal. That pins "exactly as if SSSF's refusal were its result".
2. Three denials, as three SSSF refusals:
   - **2a** `test_three_denials_by_claude_code_stop_at_max_refusals`. Use
     `agent_rules=[{"rule": "Never cat .env.", "forbid": [r"\bcat\s+\.env\b"],
     "on_match": "refuse"}]`; that repo rule is first in `enforced`, so it is
     counted. Run three `_denied_bash` calls (ids `toolu_den1..3`).
     - `pytest.raises(rules.RuleBreach, match="Nothing refused ran")`.
     - `refusals == rules.MAX_REFUSALS`, and `partial` is set.
     - The same stream built with `_refused_bash` raises the same way, with the
       same `refusals`.
   - **2b** `test_three_denials_under_the_builtin_rule_never_stop_the_run`. Use
     only the built-in rule and three `_denied_bash` calls.
     - No raise, and three `rule_refused` events, each `refusals == 0`. Three
       `_refused_bash` calls give the same.
3. `test_a_denial_for_another_call_does_not_excuse_a_call_that_ran`:
   - The stream: a `tool_use` Grep (`id` `toolu_01BdBmiNqfMBM4JPshZVxodW`,
     path `/repo/.env`), then a `tool_use` Bash `cat .env` with id `toolu_ran`.
   - Then the Grep's `permission_denied` event verbatim from the previous fixture
     (id `toolu_01BdBmiNqfMBM4JPshZVxodW`).
   - Then a `tool_result` for `toolu_ran` with content `APP_CANARY=x`.
   - Only the built-in rule.
   - `pytest.raises(rules.RuleBreach, match="may have run")`.
4. `test_a_stop_rule_match_stops_the_run_even_when_claude_code_denies_it`:
   - Use `agent_rules=[{"rule": "Never cat .env.", "forbid":
     [r"\bcat\s+\.env\b"]}]` (stop mode, the default), and stream
     `_denied_bash("toolu_stop")`.
   - `pytest.raises(rules.RuleBreach, match="may have run")`, and no
     `rule_refused` event was recorded.
   - Also feed the forwarder directly (no agent_cc) with the `permission_denied`
     event *before* the `tool_use` event, and assert that it still raises at the
     `tool_use`.

In `test_rules.py`, in the `# ── on_match: refuse ──` section, add
`test_denied_reads_only_claude_codes_permission_denied_event`:

- `rules.denied(<the request's event>) == ["toolu_011E3m58bgpqe7LGELGbwmZ2"]`.
- `[]` for each of these:
  - a `system` `init` event;
  - a `permission_denied` event without `tool_use_id`;
  - one whose `tool_use_id` is `None` or an int;
  - a `user` event holding a `tool_result` whose content is the denial text;
  - pi's `tool_execution_end` (`finish("c1", "x")`).
- With the existing `forwarder()` helper (the `REFUSE` push rule):
  `announce("c1")`, then `{"type": "system", "subtype": "permission_denied",
  "tool_use_id": "c1"}`, then `finish("c1", "Permission denied")`. That logs one
  `rule_refused` with `refusals == 1`, which shows that a counted rule counts.

Imports needed in `test_agent_cc.py`: `yaml`, `SimpleNamespace`, and
`secret_files`, `rules` from `adw_modules`, plus `RepoSettings` from
`adw_modules.repo_settings`.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: the Secret files bullet and
  backstop sentence, plus the on_match/Claude Code paragraph (step 4).
- `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` module
  docstring (step 3).
- `.claude/skills/sssf/templates/adws/adw_modules/agents.py`
  `_event_forwarder` docstring, and the `rules.py` module docstring (steps 1–2).
- `.claude/skills/sssf/templates/repo.yaml` secret_files comment (step 5).
- There is no CHANGELOG in this repo or the skill. README has no usage text for
  this.
- For the documenter: any write-up of run 05259b95's change (6ef1833) that says
  SSSF *ignores* `permission_denied` now describes old behaviour. Note it; don't
  edit `app_docs/`. The specs `specs/05259b95_cc-permission-denied*.md` say "no
  new handling". They are records and stay as they are.

## Verification

From the repo root:

1. `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_cc.py tests/test_rules.py tests/test_secret_files.py -q -p no:cacheprovider`
   Every test passes, the new ones included. Before the code change, tests 1
   and 2a failed with `RuleBreach … may have run` (step 6 says how to see this).
2. The repo's quality suite (`adws/adw_modules/quality.py` runs the skill's whole
   `tests` folder and `adws/tests`) passes. That shows the existing refuse, kill and
   forbid tests are unchanged.
3. `git diff 635ef9c -- .claude/skills/sssf/templates/adws/adw_modules/agents.py`
   shows only the `denied` set, its update and discards, the changed condition
   and the docstring. The stop branch and the `can_refuse` path are untouched.
4. `grep -n "SSSF ignores it" .claude/skills/sssf/references/config.md .claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`
   finds nothing: exit status 1.

## Out of scope

These are old holes. A reviewer should file them as such.

- Everything the secret-file guard's task files list
  (`specs/tasks/guard-env-read.md`, `specs/tasks/guard-env-read-2.md`):
  - obfuscated access (`cat .e''nv`, a path built at runtime, a Python
    one-liner);
  - a secret file created after the agent started;
  - a grep over a whole folder;
  - secrets in the engineer's shell environment that `.env` doesn't name;
  - VM runs;
  - case variants on a case-insensitive filesystem;
  - `.env` behind a symlinked folder;
  - hard links.
- The rest of what config.md already lists as open: Edit and Write reading a
  file to edit it, files under pruned folders, deny paths with unescaped
  pattern characters.
- Claude Code emitting `permission_denied` and then running the call anyway.
  SSSF trusts the event by design (see Threat model).
- A `permission_denied` event that arrives *after* its call's `tool_result`. It
  isn't seen in the live runs, and it fails closed (`RuleBreach`) as in the baseline.
- No new trace field saying who refused (SSSF or Claude Code). The
  `rule_refused` payload is unchanged.

## For the engineer

- `adws/adw_modules/` (the installed copy of `rules.py`, `agents.py` and
  `agent_cc.py`) is protected. After the merge, update this repo's SSSF from the
  skill as usual ("Update SSSF to skill <sha>").
- Rerun both live proofs in sssf-sandbox after the merge: the Read/Grep `.env`
  denial (34fb3c8c-style) and the Bash `cat .env` denial (ee920adc-style). Each
  agent phase should go on, with a `rule_refused` event for the Bash call and no
  `rule_breach`.
