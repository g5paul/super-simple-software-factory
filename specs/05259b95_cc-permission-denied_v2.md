# Plan: don't crash on Claude Code's `permission_denied` event

## Summary

Claude Code 2.1.319 emits a `system` event with `subtype: "permission_denied"`
for each call the secret-file guard's `--disallowedTools "Read(//<abs>)"`
refuses. Its `message` is a **string**. `ToolCallTracker.observe` in
`.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` does
`(event.get("message", {}) or {}).get("content", [])`, which raises
`'str' object has no attribute 'get'` and kills the agent phase (runs 34fb3c8c,
ee920adc). The fix: every read of an event's `message` in `agent_cc.py` goes
through one helper that returns `{}` for anything that is not a dict.

## Threat model

- The agent runs as the engineer's own user, under `bypassPermissions`. It can
  run any shell command, read any file the user can, and write anywhere. What
  it may change is checked after the call by `permissions.py`. What it may
  read is limited only by the deny rules, `sssf_refuse.py` and the stream check.
- This guard (the secret-file deny) is aimed at **accidental** exposure. It
  cannot stop an agent that deliberately builds the path, copies the file
  first, or uses a tool Claude Code's rule doesn't cover. At this level SSSF
  only reads the event stream; it does not sandbox the process.
- This change touches only how SSSF parses the stream. The Claude Code CLI
  produces each stream event; the agent doesn't write them, but the model's
  output shapes them. The parser must tolerate any JSON shape without raising,
  and must treat a malformed event as "no content" and nothing else.

## Baseline

`main` at `635ef9c` ("Update SSSF to skill a3c2536: keep secret files and .env
keys from agents"). Every finding is judged against it. At the baseline the
deny works, and a `permission_denied` event crashes the call (fails closed;
nothing leaks).

## Bar

The change must:

- Let a Claude Code call whose stream holds `permission_denied` system events
  (string `message`) run to its normal `result` and return an `AgentResult`.
- Keep recording a denied call as a failed tool call (`ok: False`) in the
  trace, from its `tool_result` block with `is_error: true`, exactly as today.
- Within `agent_cc.py`'s own parsing (`ToolCallTracker.observe` and the stream
  loop in `run`), not raise on an `assistant` event, or on a `stream_event`'s
  inner `message_start`, whose `message` is a string or any other non-dict.
  (`rules.calls` still can; see Out of scope.)

The change must never, against the baseline:

- Let a protected file's content reach the agent (the `--disallowedTools`
  flags in `_command` stay exactly as they are).
- Keep any agent file, run any agent code, or delete or overwrite an engineer
  file.
- Add any new handling of `permission_denied` events: no new trace event, log,
  counter or stop. They are ignored like any other unhandled `system` event.
- Change behaviour for any event whose `message` is a dict.

## Files to change

### `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

1. Add a module-level helper next to `_text_of` (around line 149):

   ```python
   def _message(event: dict) -> dict:
       """An event's `message`, or {} when it isn't a dict. Claude Code 2.1.319's
       permission_denied system event carries a string there, one per denied call."""
       message = event.get("message")
       return message if isinstance(message, dict) else {}
   ```

2. `ToolCallTracker.observe` (line 170): replace
   `(event.get("message", {}) or {}).get("content", [])` with
   `_message(event).get("content") or []`. Keep the existing
   `isinstance(b, dict)` filter. Add no other guard.

3. Stream loop in `run`:
   - line 308: `(inner.get("message") or {}).get("id")` → `_message(inner).get("id")`.
   - line 337: `message = event.get("message") or {}` → `message = _message(event)`.
   - line 343: `event.get("message", {}).get("usage")` → `_message(event).get("usage")`.

   Leave everything else in the loop unchanged. No new branch for
   `subtype == "permission_denied"`.

4. Module docstring (lines 20–23, the secret-file sentence): add one short
   sentence. Each denial also arrives as a `permission_denied` system event
   whose `message` is a string. SSSF ignores it, and the trace records the
   denied call from its `tool_result`.

Do not touch `rules.py`, `agents.py`, or `agent_pi.py` (see Out of scope).

### `.claude/skills/sssf/tests/test_agent_cc.py`

Add, in the `# ── tool calls ──` section after
`test_tool_result_content_may_be_a_block_list`:

1. `test_a_denied_read_is_a_failed_call_and_the_call_goes_on(tmp_path, fake)`:
   - Build the stream in the test (no new fixture file): take the first line
     (`system`/`init`) and the last line (`result`) of
     `FIXTURES / "cc_turn1.jsonl"`, and between them put, in order:
     - an `assistant` event whose `message` is a dict (`id`, `model`,
       `role: "assistant"`, `usage` copied from any assistant line of
       cc_turn1 is fine) with one `tool_use` block:
       `{"type": "tool_use", "id": "toolu_01BdBmiNqfMBM4JPshZVxodW", "name": "Read", "input": {"file_path": "/repo/.env"}}`;
     - a `user` event with one block:
       `{"type": "tool_result", "tool_use_id": "toolu_01BdBmiNqfMBM4JPshZVxodW", "is_error": true, "content": "<tool_use_error>File is in a directory that is denied by your permission settings.</tool_use_error>"}`;
     - the two `permission_denied` events verbatim from the request:

       ```
       {"type": "system", "subtype": "permission_denied", "tool_name": "Grep", "tool_use_id": "toolu_01BdBmiNqfMBM4JPshZVxodW", "decision_reason_type": "rule", "message": "Permission to read /repo/.env has been denied.", "uuid": "bacf0074-14fc-4c40-9871-972f71ae02f7", "session_id": "2b34dc8b-a340-4c0c-82bb-046a7632da0a"}
       {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "toolu_011E3m58bgpqe7LGELGbwmZ2", "decision_reason_type": "rule", "message": "Permission to use Bash with command cat .env has been denied.", "session_id": "2b34dc8b-a340-4c0c-82bb-046a7632da0a"}
       ```
   - Write it to `tmp_path / "denied.jsonl"`, point `fake(...)` at it, and run
     `agent_cc.run(request(tmp_path), on_event=...)`. The `on_event` callback
     extends a records list with `tracker.observe(e)` and also calls
     `rules.calls(e)` and `rules.results(e)` (import `rules` from
     `adw_modules`). That way the replay covers the readers the real
     `agents._forward` path runs on each event.
   - Assert: `run` returns (no exception) with `result.text` equal to the
     fixture result's `result`; exactly one record; `records[0]["tool"] ==
     "Read"`, `records[0]["ok"] is False`, `"denied by your permission
     settings" in records[0]["result_snippet"]`.

2. `test_an_assistant_message_that_is_not_a_dict_does_not_raise(tmp_path, fake)`:
   - Direct tracker check: `agent_cc.ToolCallTracker().observe({"type": "assistant", "message": "oops"}) == []`. Check the same for a `user` event with a string `message`, and for a `system` permission_denied event.
   - Run check: a stream of cc_turn1's init line, `{"type": "assistant", "message": "oops"}`, a `stream_event` `{"type": "stream_event", "event": {"type": "message_start", "message": "oops"}}`, then cc_turn1's result line. `agent_cc.run(request(tmp_path), on_event=lambda e: tracker.observe(e))` returns without raising. Do **not** call `rules.calls` here (see Out of scope).

Neither test needs an SSSF config, so no config is built. If the builder does
need one, build it from `.claude/skills/sssf/templates/sssf.config.yaml`,
never a test-only one.

### `.claude/skills/sssf/references/config.md`

In `## Secret files — secret_files and agent_env`, extend the **Claude Code**
bullet (line 417) with one sentence. It says that Claude Code also emits a
`permission_denied` system event for each refusal, that SSSF ignores it, that
the agent reads the refusal and goes on, and that the trace records the call
as a failed tool call. Leave the known-limits paragraph (line 425) unchanged.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: the Secret files section (above).
- `agent_cc.py` module docstring (above).
- This repo keeps no CHANGELOG. No README usage or help text changes.
- `app_docs/`: not edited by this plan. The documenter may find that the
  write-up for the secret-file guard (merge 9bef4a4) says a denied Claude Code
  read ends the run, or doesn't mention it. If so, the documenter notes that
  the run now goes on.

## Verification

1. The repo's own test quality check, which runs the skill suite and with it
   the new tests:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
   should exit 0.
2. Focused run: the same command with `tests/test_agent_cc.py` in place of
   `tests`. It should exit 0, with the two new tests passing.
3. Write the tests before editing `agent_cc.py` and run them once: test 1 fails
   with `AttributeError: 'str' object has no attribute 'get'` on the baseline
   code, then passes after the change.
4. `git diff --stat` shows only `agent_cc.py`, `test_agent_cc.py`, and
   `config.md` under `.claude/skills/sssf/`. Nothing under `adws/`.

## Out of scope

Old holes this change leaves open, to be filed as such:

- Everything the secret-file guard's task files list
  (`specs/tasks/guard-env-read.md`, `specs/tasks/guard-env-read-2.md`):
  obfuscated access (`cat .e''nv`, a built path), a secret file created after
  the agent starts, a grep over a whole folder, secrets in the engineer's own
  shell environment, VM runs, case variants on a case-insensitive filesystem,
  `.env` behind a symlinked folder, and hard links.
- Every known limit in config.md's Secret files section
  (`.claude/skills/sssf/references/config.md:425`, "This stops accidental
  exposure…"), by reference. That includes the Claude Code deny limits that
  neither task file lists:
  - Edit and Write reading a file in order to edit it. The deny covers Read
    and Grep only.
  - `/proc/<pid>/environ`: the agent runs as the engineer's user and can read
    the environment of the engineer's processes.
  - Files under the pruned folders (the data dir, `.git`, `node_modules`,
    `.venv`, `venv`, `__pycache__`), and a secret file in a folder the search
    can't read. Neither gets a deny.
  - Deny paths with pattern characters that `deny_rule` doesn't escape, such
    as `(`, `)`, `,` or `#`. Such a path may not be denied, and the file stays
    as readable as at the baseline.
  - The operator container mounts the repo and its `.env`, so it is no
    boundary for `.env`.
- `rules.calls` (`.claude/skills/sssf/templates/adws/adw_modules/rules.py:107`)
  still does `(event.get("message") or {}).get("content")` on `assistant` and
  `message_end` events, and `rules.results` (line 130) does the same on
  `message_end`/`user`. A `permission_denied` (`system`) event never reaches
  those `.get` calls, so the observed crash doesn't come from there. An
  `assistant` or `user` event with a string `message` would still raise there,
  which stops the run (fails closed). Not observed live; left as is.
- `agent_pi.py`'s own parsing is unchanged.
- No new handling of `permission_denied` (trace event, count, stop).

## For the engineer

- `adws/adw_modules/agent_cc.py` is this repo's installed copy and is
  protected. After the merge, update the repo's SSSF from the skill (the usual
  "Update SSSF to skill <sha>" step, `just sssf-update`) so the installed copy
  gets the fix.
- Rerun both live proofs in sssf-sandbox on the new skill commit: the
  Read/Grep proof of 34fb3c8c and the Bash `cat .env` proof of ee920adc.
  Confirm that each agent phase ends normally and that the denied call is
  recorded as a failed tool call.
