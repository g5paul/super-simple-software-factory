# Word the secret-file refusals for what they refused

## Summary

Two refusals from SSSF's secret-file guard use the wrong words. Both block what they should. Only the text the agent reads changes:

1. A shell command refused by SSSF's **built-in** secret-file rule says "by this repo's rule". It should say "by SSSF's secret-file rule".
2. A refused pi `read`/`grep`/`find`/`ls` call says "SSSF refused this command". It should say "SSSF refused this <tool> call".

`rules.RuleBreach` gets the same fix: it names the built-in rule as SSSF's. Nothing changes about what is refused, counted or stopped.

All code lives in the skill template, `.claude/skills/sssf/templates/adws/adw_modules/`. The hub's installed copy, `adws/adw_modules/`, is protected and is left to the engineer (see the last section).

## Baseline

`main` at **`077e035`** ("Track the secret-file refusals' wording in open.md"). Every finding is judged against that commit. `HEAD` (`d18084e`) adds only `specs/tasks/guard-refusal-wording.md`, so the guard code is the same.

What the baseline does:

- `secret_files.shell_rule(files)` builds an `AgentRule(rule=RULE_TEXT, on_match="refuse", forbid=[...])`. `agents.py` puts it in `enforced` and passes it as `uncounted=(builtin,)`. `uncounted` is matched by identity (`rule is u`), so a repo rule with the same text still counts (`test_a_repo_rule_equal_to_the_built_in_one_still_counts`).
- `rules.refusing(enforced)` turns the refuse rules into `[{"rule", "forbid"}]`. That goes to `SSSF_REFUSE_RULES` for both `sssf_refuse.py` (Claude Code) and `sssf_rules.ts` (pi). Neither script can tell the built-in rule from a repo rule, so both say "by this repo's rule: <text> (it matches <regex>)".
- `sssf_rules.ts` blocks a file tool on a secret path with `${REFUSED}: <path> is one of this repo's secret files, ... Nothing ran. ...`.
- `rules.refused(text)` checks for the `REFUSED` prefix (or `Exit code 126\n` + `REFUSED`). `agents._event_forwarder` only calls it on the result of a call id in `pending`. Only `rules.calls(event)` adds to `pending`, and it returns **shell tools only** (`SHELL_TOOLS = {"Bash", "bash"}`). Any other result is dropped at `if call_id not in pending: ... continue`.

**Constraint check (done while planning):** the result text of a pi file-tool refusal never reaches `rules.refused` for a call a rule matched. A `read`/`grep`/`find`/`ls` call never gets into `pending`, because `rules.calls` filters by `SHELL_TOOLS`. So the new file-tool text does not need to start with `REFUSED`, and it doesn't. Step 6 adds a test that pins this down.

## Threat model

The agent runs as the engineer's own user. It can run any shell command, use pi's file tools, build paths at runtime, read `/proc/<pid>/environ`, and so on. This guard exists to stop **accidental** exposure of `.env` and similar files (see `secret_files.py`'s docstring). It is not a boundary against an agent that is set on reading a secret. The VMs are the boundary.

This change only alters the text the agent reads after a refusal, plus one boolean in the `SSSF_REFUSE_RULES` JSON. Can the agent use that boolean? `SSSF_REFUSE_RULES` is in the agent's own environment, and the agent can't change the environment of the prefix or extension that is already running. Even if it could, it could already empty the rules list at baseline, so the boolean adds nothing. A repo can't mark its own rule as built-in: `AgentRule` is `extra="forbid"`, and `refusing()` sets the flag only for the one rule object `agents.py` built (identity). The stream check (`rules.breach` + `RuleBreach`) stays the backstop, unchanged.

## Bar

Against `077e035`, the change **must**:

- Start every shell refusal's result with `rules.REFUSED` ("SSSF refused this command"), on both harnesses, so `rules.refused` and `_CLAUDE_REFUSED` still match it.
- Word the built-in rule's shell refusal as `SSSF refused this command, by SSSF's secret-file rule: <RULE_TEXT> Nothing ran. Don't try it another way: do the task without it, or say in your report that the task needs it.`
- Leave a repo refuse rule's refusal byte-for-byte as it is today: `SSSF refused this command, by this repo's rule: <rule> (it matches "<pattern>"). Nothing ran. Don't try it another way: ...`. This holds even for a repo rule whose text equals `RULE_TEXT`, which keeps the repo wording.
- Word a pi file-tool refusal as `SSSF refused this <tool> call: <path as given> is one of this repo's secret files, which SSSF keeps from agents. Nothing was read. Don't try it another way: do the task without it, or say in your report that the task needs it.`
- Make `RuleBreach` say "SSSF's secret-file rule" in both messages (the breach one and the refusals one) when the rule is the built-in one by identity, and keep today's text otherwise.

It must **never**:

- Change `rules.REFUSED`, `_CLAUDE_REFUSED`, `rules.refused`, or what is refused, counted (`uncounted`, `MAX_REFUSALS`) or stopped.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline does otherwise. Nothing here touches files or process launch.
- Put a backslash in `sssf_refuse.py`'s module docstring (a warning on stderr would hide the refusal).

## Design

The scripts learn which rule is the built-in one from a flag in the JSON, set by identity in `rules.refusing`:

- `rules.refusing(rules, builtin=None)` adds `"builtin": True` to the dict for the rule that `is builtin`. Every other rule's dict stays `{"rule", "forbid"}`.
- `sssf_refuse.py` and `sssf_rules.ts` pick the wording from `rule.builtin`.
- `RuleBreach(..., builtin=False)` picks its wording from a keyword argument. `_event_forwarder` passes `builtin=any(rule is u for u in uncounted)`, the identity test it already uses for counting.

The pattern clause is dropped for the built-in rule. Its pattern is an escaped regex (`(?<![\w.-])(?:\.env)(?![\w.-])`), which reads worse than the rule text. `RULE_TEXT` already tells the agent the command named one of the repo's secret files. RULE_TEXT ends in "agents.", so the next sentence follows after one space.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

- Add a module constant next to `REFUSED`:
  `BUILTIN_RULE = "SSSF's secret-file rule"   # how a refusal or breach names secret_files.shell_rule's rule`
- Reword the comment above `REFUSED` to "How sssf_rules.ts and sssf_refuse.py start a shell refusal, so the stream check can tell it from output. Claude Code puts the exit line first. A pi file-tool refusal says "this <tool> call" instead, and never reaches the stream check: only shell calls are." Keep `REFUSED` and `_CLAUDE_REFUSED` exactly as they are.
- `RuleBreach.__init__`: add a keyword-only parameter `builtin: bool = False` after `refusals`, and store `self.builtin = builtin`. Set `whose = BUILTIN_RULE if builtin else "this repo's rule"`. Then:
  - refusals message: `f"SSSF refused {agent} {refusals} times for {whose}: {rule.rule} "` + the rest unchanged.
  - breach message: `f"{agent} ran `{command[:300]}`, which {whose} forbids: {rule.rule} "` + the rest unchanged.
  - With `builtin=False`, both strings must come out identical to the baseline.
- Change the class docstring to: "An agent ran a command one of this repo's agent_rules, or SSSF's built-in secret-file rule, forbids."
- `refusing(rules, builtin=None)`: add `builtin: Optional[AgentRule] = None`. Each dict is `{"rule": rule.rule, "forbid": rule.forbid}`, plus `"builtin": True` only when `rule is builtin`. Extend the docstring: "`builtin` (secret_files.shell_rule's rule, by identity) is marked so the refusal names it as SSSF's."

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

- Line ~227: `refuse_rules=rules.refusing(enforced, builtin=builtin),`. `builtin` is already in scope from `_secret_guard`, and may be `None`.
- `_event_forwarder`: add a local helper `is_builtin = lambda rule: any(rule is u for u in uncounted)`. Use it for the existing count test, and pass `builtin=is_builtin(rule)` (or `is_builtin(found[0])`) to all three `rules.RuleBreach(...)` raises: the announce-time raise, the "may have run" raise, and the `refusals=` raise. Add one docstring sentence: "A RuleBreach for a rule in `uncounted` names it as SSSF's secret-file rule."

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sssf_refuse.py`

- In `refusal()`, when `rule.get("builtin")` is set, return
  `f"{REFUSED}, by SSSF's secret-file rule: {rule.get('rule')} Nothing ran. Don't try it another way: do the task without it, or say in your report that the task needs it."`
  Otherwise return today's string exactly. Build the shared tail ("Nothing ran. Don't try ...") once so the two can't drift.
- Docstring: add one sentence: "SSSF's built-in secret-file rule comes marked `builtin`, and its refusal names it as SSSF's." **No backslashes.**

### 4. `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`

- `interface Rule { rule: string; forbid: string[]; builtin?: boolean }`. Carry `builtin: !!r.builtin` into `compiled`.
- Bash branch: if `builtin`, the reason is `${REFUSED}, by SSSF's secret-file rule: ${rule} ` + tail. Otherwise it is today's string exactly. The tail is the same `"Nothing ran. Don't try it another way: do the task without it, or say in your report that the task needs it."` Share it through a `const`.
- File-tool branch: the reason is
  `` `SSSF refused this ${event.toolName} call: ${given} is one of this repo's secret files, which SSSF keeps from agents. Nothing was read. Don't try it another way: do the task without it, or say in your report that the task needs it.` ``
- Change the comment above `const REFUSED` to "rules.REFUSED in Python: the stream check looks for it in a bash call's result. File-tool refusals don't use it; the stream check never reads those."
- Header docblock: add "SSSF's built-in secret-file rule comes marked `builtin`, and its refusal names it as SSSF's."

### 5. `.claude/skills/sssf/references/config.md` (Secret files section, lines ~415–419)

- **pi** bullet: append "The agent reads 'SSSF refused this read call: .env is one of this repo's secret files…' (or grep, find, ls), and nothing is read."
- **Shell** bullet: append "Its refusal reads 'SSSF refused this command, by SSSF's secret-file rule: …', so the agent can tell it from a repo's own rule."

### 6. Tests

Every new test that needs a config or repo settings builds them from what SSSF ships: `templates/sssf.config.yaml` (through `test_secret_files.py`'s `shipped_config()`), and `templates/repo.yaml` (through `shipped_settings()`, or `test_agent_cc.py`'s `_guarded`, which reads it). Repo rules are added on top of the shipped `repo.yaml` data, as `_guarded` already does. No test-only config file is written. Unit tests of pure text (`refusing()` output, `RuleBreach` strings, the prefix and extension text) take the built-in rule from `secret_files.shell_rule(...)`, which is the same object `agents._secret_guard` returns.

**One existing assertion must change:** `test_the_pi_extension_blocks_secret_file_reads_and_the_built_in_pattern` in `.claude/skills/sssf/tests/test_rules.py` (line ~554) asserts that each file-tool block's reason `startswith(rules.REFUSED)`. The request requires the new "this read call" wording, which can't start with that prefix. The constraint check above shows the prefix isn't needed there. Replace only that loop's assertion with the new-text checks below. Every other existing assertion stays as it is, and the rest of each file must pass unchanged.

#### 6a. The wiring through `agents.execute`, from the shipped config: `.claude/skills/sssf/tests/test_secret_files.py`

New `test_execute_marks_only_the_built_in_rule_in_the_requests_refuse_rules(tmp_path, monkeypatch)`. It runs `agents.execute`, the real launch path, so dropping `builtin=builtin` at `agents.py:227` fails it.

- `cfg = shipped_config()`. `root = make_repo(tmp_path / "repo")` gives `.env` and `sub/.env.local`. `monkeypatch.chdir(root)`: `execute` reads settings from `Path.cwd()`.
- Repo settings: `data = yaml.safe_load((TEMPLATES / "repo.yaml").read_text()) or {}`, and set `data["agent_rules"]` to:
  - `{"rule": "Never run `git push`.", "forbid": [r"\bgit\s+push\b"], "on_match": "refuse"}` (a repo refuse rule);
  - `{"rule": secret_files.RULE_TEXT, "forbid": [r"\bcat\s+\.env\b"], "on_match": "refuse"}` (a repo twin with the built-in rule's text);
  - `{"rule": "Never run rm -rf.", "forbid": [r"\brm\s+-rf\b"]}` (a stop rule, which must not appear in `refuse_rules`).
  Write it with `yaml.safe_dump` to `root / repo_settings.PATH` (`adws/adw_sssf_config/repo.yaml`, parents created). Import `repo_settings` from `adw_modules`.
- Prompt files: for each agent in `cfg.agents`, touch `root / a.prompt_engineering.system` and `.user`, parents created, as `test_agent_pi.py`'s `_shipped` does.
- A fake interface that records the request and stops before anything spawns:
  `class Captured(Exception): pass`; `seen = []`; `def run_fake(request, **kw): seen.append(request); raise Captured()`; `fake = SimpleNamespace(run=run_fake, ToolCallTracker=agent_pi.ToolCallTracker)`; `monkeypatch.setattr(agents, "_interface", lambda agent: fake)`.
- Stub, with `monkeypatch.setattr` on the names as `agents` reaches them, only the side effects outside this guard that run before the send:
  - `agents.budget.check_run`, `check_live`, `check_daily` and `check_machine` → no-op;
  - `agents.permissions.guard_git_dir` → `lambda run: None`;
  - `agents.permissions.restore_git_dir` → `lambda *a, **k: []`;
  - `agents.agent_cc.check_login` and `agents.account.check` → no-op, in case the resolved agent is a Claude Code one.
  If `execute` reaches another such call before `interface.run`, stub it the same way. **Never change `execute` to suit the test.**
- `run = SimpleNamespace(cfg=cfg, adw_id="r1", session_dir=tmp_path / "s", repo_root=root, context_handoff_dir=tmp_path / "h", agent_map={}, tracer=SimpleNamespace(event=lambda e: None, process_start=lambda *a: None, process_end=lambda *a: None, gate_row=lambda *a: None, agent_session_row=lambda *a, **k: None), console=SimpleNamespace(agent_started=lambda *a: None, advise=lambda *a: None, note=lambda *a: None, envelope_summary=lambda *a: None), add_usage=lambda *a: None, save_agent_map=lambda *a: None)`. Add any other attribute `execute` reads before the send as a no-op, the same way.
- `phase = SimpleNamespace(phase_id="r1_01", params=SimpleNamespace(owner="builder", name="build"))`, `call = SimpleNamespace(prompt="x", previous=None)`. Run `with pytest.raises(Captured): agents.execute(run, phase, call)`.
- Assert on `[request] = seen` and `got = request.refuse_rules`:
  - `[r for r in got if r.get("builtin")]` is exactly one entry: `{"rule": secret_files.RULE_TEXT, "forbid": secret_files.shell_rule(find_files).forbid, "builtin": True}`, where `find_files = [str(root / ".env"), str(root / "sub/.env.local")]`;
  - the repo push rule's entry is `{"rule": "Never run `git push`.", "forbid": [r"\bgit\s+push\b"]}`, with no `builtin` key;
  - the twin's entry is `{"rule": secret_files.RULE_TEXT, "forbid": [r"\bcat\s+\.env\b"]}`, with no `builtin` key;
  - no entry has the rm rule's text.

#### 6b. The forwarder's breach, through `_guarded`: `.claude/skills/sssf/tests/test_agent_cc.py`

`_guarded(root, agent_rules=())` builds the forwarder the way `agents.py` does, from the shipped `repo.yaml`. Add a keyword `can_refuse=True` to it and pass it through to `agents._event_forwarder`. The default keeps every existing caller's behaviour.

New `test_a_breach_names_the_built_in_rule_as_sssfs_and_a_repo_rule_as_the_repos(tmp_path)`:

- `forward, _, builtin = _guarded(tmp_path / "a")`; `forward(_bash_call("t1"))` (`cat .env`); `with pytest.raises(rules.RuleBreach) as stop: forward(_result("t1", "APP_CANARY=x"))`. Assert that `"which SSSF's secret-file rule forbids: " + secret_files.RULE_TEXT` is in `str(stop.value)`, `"this repo's rule" not in str(stop.value)`, and `stop.value.builtin is True`.
- `forward, _, _ = _guarded(tmp_path / "b", can_refuse=False)`. `with pytest.raises(rules.RuleBreach, match="SSSF's secret-file rule forbids"): forward(_bash_call("t2"))`. This is the announce-time raise.
- `forward, _, _ = _guarded(tmp_path / "c", agent_rules=[{**CAT_RULE, "on_match": "refuse"}])`. The repo rule comes first in `enforced`, so it is the one found. `forward(_bash_call("t3"))`; finishing with `_result("t3", "APP_CANARY=x")` raises a `RuleBreach` whose `str` equals the baseline text exactly: `"builder ran `cat .env`, which this repo's rule forbids: Never cat .env. (matched '\\bcat\\s+\\.env\\b'). SSSF killed the agent and everything it started, but the command may have run first: check what it did before resuming."` (build it with `f"... (matched {CAT_RULE['forbid'][0]!r}) ..."`). Also check `.builtin is False`.

The existing `test_three_denials_by_claude_code_stop_at_max_refusals` covers the repo rule's refusals message. Add one assertion to it: `"times for this repo's rule: Never cat .env." in str(caught[0])`.

#### 6c. Text units: `.claude/skills/sssf/tests/test_rules.py`

- In `test_the_pi_extension_blocks_secret_file_reads_and_the_built_in_pattern`, replace the `for blocked in (first, second, third)` assertion with:
  - all three have `block is True`;
  - `first["reason"] == "SSSF refused this read call: .env is one of this repo's secret files, which SSSF keeps from agents. Nothing was read. Don't try it another way: do the task without it, or say in your report that the task needs it."`;
  - `second["reason"].startswith("SSSF refused this read call: @.env is one of this repo's secret files")`;
  - `third["reason"].startswith("SSSF refused this grep call: .env is one of this repo's secret files")`;
  - `"command" not in` each reason.
  - In its second half, pass `rules.refusing([rule], builtin=rule)` where `rule = secret_files.shell_rule(files)`. Then assert that `cat_env["reason"].startswith(rules.REFUSED + ", by SSSF's secret-file rule: " + secret_files.RULE_TEXT + " Nothing ran.")` and that `"this repo's rule" not in cat_env["reason"]`.
- `test_the_pi_extension_blocks_a_matching_bash_call_with_the_rule` (bun): add `blocked["reason"] == f"{rules.REFUSED}, by this repo's rule: {REFUSE.rule} (it matches {json.dumps(REFUSE.forbid[0])}). Nothing ran. Don't try it another way: do the task without it, or say in your report that the task needs it."`. This is the unchanged repo wording.
- New `test_the_prefix_names_the_built_in_rule_as_sssfs`: `builtin = secret_files.shell_rule(["/r/.env"])`; `done = prefix(claude_line("cat .env"), json.dumps(rules.refusing([builtin], builtin=builtin)))`. Assert:
  - `returncode == 126`;
  - `done.stderr.startswith(rules.REFUSED + ", by SSSF's secret-file rule: " + secret_files.RULE_TEXT + " Nothing ran.")`;
  - `"this repo's rule" not in done.stderr`;
  - `rules.refused(f"Exit code 126\n{done.stderr}")`.
  Then pass a twin (`builtin.model_copy()`) as `refusing([twin], builtin=builtin)`, and assert its stderr has `", by this repo's rule: "` (identity, not text).
- New `test_the_prefix_keeps_a_repo_rules_wording`: `prefix(claude_line("git push"), json.dumps(rules.refusing([REFUSE])))`. The stderr must equal the exact baseline string above + `"\n"`.
- New `test_refusing_marks_only_the_built_in_rule`: `builtin = secret_files.shell_rule(["/r/.env"])`, `twin = builtin.model_copy()`. Then `rules.refusing([REFUSE, builtin, twin], builtin=builtin)` must equal `[{"rule": REFUSE.rule, "forbid": REFUSE.forbid}, {"rule": builtin.rule, "forbid": builtin.forbid, "builtin": True}, {"rule": twin.rule, "forbid": twin.forbid}]`, and `rules.refusing([REFUSE])` has no `builtin` key.
- New `test_a_breach_message_names_the_built_in_rule_as_sssfs`:
  - `str(rules.RuleBreach("builder", builtin, builtin.forbid[0], "cat .env", builtin=True))` contains `"which SSSF's secret-file rule forbids: "` and not `"this repo's rule"`;
  - with `refusals=3, builtin=True` it contains `"times for SSSF's secret-file rule: "`;
  - `str(rules.RuleBreach("builder", PUSH, PUSH.forbid[0], "git push origin main"))` equals the baseline text exactly: `f"builder ran `git push origin main`, which this repo's rule forbids: {PUSH.rule} (matched {PUSH.forbid[0]!r}). SSSF killed the agent and everything it started, but the command may have run first: check what it did before resuming."`;
  - the `refusals=3` repo form equals its baseline text exactly.
- New `test_a_file_tool_refusal_never_reaches_the_stream_check`: this pins the constraint. Use `forwarder(enforced=[builtin], uncounted=(builtin,))`. Send `{"type": "tool_execution_start", "toolCallId": "r1", "toolName": "read", "args": {"path": ".env"}}`, then `{"type": "tool_execution_end", "toolCallId": "r1", "toolName": "read", "result": {"content": [{"type": "text", "text": "SSSF refused this read call: .env is one of this repo's secret files, which SSSF keeps from agents. Nothing was read."}]}, "isError": True}`. Assert no exception and `events == []`.

### 7. `docs/open.md`

Delete the row "The secret-file refusals' wording. …" (line 19). The file's own rule: "When an item closes, delete its row."

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md` Secret files section: step 5.
- Docstrings and comments in `rules.py`, `agents.py`, `sssf_refuse.py`, `sssf_rules.ts`: steps 1–4.
- `docs/open.md`: step 7.
- No CHANGELOG in this repo. No README or help text mentions the wording. `config.md` line ~395 ("The agent reads 'SSSF refused this command' and the rule") is about repo rules and stays true.
- `app_docs/`: no write-up quotes either wording (searched for "is one of this repo's secret files" and "by this repo's rule"). Nothing for the documenter to flag.
- `docs/projects.md` (protected) quotes "SSSF refused this command" for repo-rule refusals. That still holds, so no change.

## Verification

From the skill root, `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -rs tests/test_rules.py tests/test_agent_cc.py tests/test_secret_files.py tests/test_sandbox_remote.py
```

All must pass. bun is on PATH, so the bun-gated tests in `test_rules.py` must run, not skip: `-rs` lists any skip. Then run the whole skill suite:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
```

Then check that `git diff 077e035 -- .claude/skills/sssf/templates/adws/adw_modules/rules.py` leaves `REFUSED`, `_CLAUDE_REFUSED` and `refused()` untouched.

Revert `agents.py:227` to `rules.refusing(enforced)` and confirm that 6a fails, then restore it. This checks the wiring test by hand and is not kept.

The visualizer test `apps/visualizer/server/runview.test.ts` quotes a repo-rule breach message, which is unchanged. No run is needed.

## Out of scope

Holes this change leaves open. They are old holes, not findings:

- Everything in `config.md`'s secret-files "left open" paragraph: path built at runtime, `/proc/<pid>/environ`, a grep over a whole folder, Edit/Write reading a file, case-insensitive filesystems, pruned folders, symlinked folders, hard links, deny-path escaping.
- A repo rule whose text equals `RULE_TEXT` is still a separate rule. It keeps the repo wording and still counts. That is intended, and the same identity rule decides both.
- Claude Code's own `permission_denied` denials keep Claude Code's wording, which SSSF doesn't write.
- What is refused, counted or stopped, `MAX_REFUSALS`, and how patterns match are all unchanged.
- The hub's installed `adws/adw_modules/` keeps the old wording until the engineer syncs it (below).

## For the engineer

- `adws/adw_modules/` is protected. After this lands, update the hub's installed SSSF to the new skill commit, the same way as `690572f` ("Update SSSF to skill 7acbd46"). That carries `rules.py`, `agents.py`, `sssf_refuse.py` and `sssf_rules.ts` into `adws/adw_modules/` and bumps `adws/adw_sssf_config/stamp.json`. Then put the listed projects on the new skill commit in `docs/projects.md`.
- A live check, optional: rerun the sssf-sandbox pi proof (as in run 6eaef389). Confirm that `cat .env` reads "by SSSF's secret-file rule", that `read .env` reads "SSSF refused this read call", and that the trace still shows `rule_refused` for the shell call.
