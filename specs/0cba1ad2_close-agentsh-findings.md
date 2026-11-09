# Close the last agentsh wrap findings

Revises `specs/8b06e76e_finish-agentsh-wrap_v2.md`. Everything in it not changed here still binds. **Keep the code on this branch and make only the changes below.** Request: `specs/tasks/guard-agentsh-wrap-3.md`. Earlier requests: `specs/tasks/guard-agentsh-wrap.md` and `specs/tasks/guard-agentsh-wrap-2.md`. Last run's report: `docs/designs/agentsh/guard-run-8b06e76e.md`. The engineer's decisions in the design doc's section "The second guard run, 8b06e76e" aren't reopened.

## Threat model

As the revised spec: the agent runs as the engineer's user, can spell anything any way, and can change what the policy lets it write, including `repo.yaml`, the worktree's `.git` file and files in `/tmp`. It can name its own executables anything, including bytes that aren't valid UTF-8, and a process can change its own name.

New here: the install files wait in a temp folder. The revised spec called a temp-folder edit contained because the launch refuses afterwards. That doesn't hold for a link. `sudo install` follows it, so an agent running during the install could get a root-only file copied to `/etc/agentsh/sssf.yaml` at mode 0644 before any refusal. The engineer decided a printed warning covers this, because a check in the printed commands would still leave a race.

What this guard can't stop at its level: the revised spec's list, plus the limits under "Out of scope". The joined exec text can't see a push that doesn't use the word, and the walk can only reach what is still parented under the wrap.

## Baseline

`main` at `815144b`: the text guard alone. Every finding is judged against it. The wrap's code on this branch, reviewed through snapshot `ed1cd23`, isn't the baseline.

## Bar

The bars in `specs/tasks/guard-agentsh-wrap.md`, `specs/tasks/guard-agentsh-wrap-2.md` and the revised spec stand, with these added:

- A process that is under the wrap at a stop and is reachable by the walk must not survive it because of its name.
- Any spelling the engineer's list says stays denied, or runs, must do so with the pattern text the generated policy holds, judged on argv joined by spaces with `argv[0]` first. That list is the revised spec's "Push cases" plus the four newline cases below.
- A broken `config_template` makes `check_machine` raise `AgentshRefused` with a message, and no agent starts.
- Default bar: no agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise.

## Out of scope

- Everything in the revised spec's Out of scope and in the earlier requests', except that the newline in a git option is now in scope and closed here.
- The session folder allow, running a real agent under the service, patching the agentsh fork, sudo, and turning this on in any repo.
- Everything else about `_kill_descendants` and its order against `rules.stop_tree`. Text mode stays as it is.
- **Accepted limits, not findings, because the engineer decided them:**
  - A stop with the chain out of file descriptors, where neither `ps` nor `/proc` can be read, kills only the wrap.
  - A signal landing inside the walk can cut it short, and `_stopped` then blocks a second attempt.
  - The double fork, and SIGKILL to the wrap.
  - The `sudo install` link once the warning prints.
  - `GIT_CONFIG_COUNT` with an alias, and `git send-pack`.
  - `mv .git .git2`, an edit and a move back, on a run without `--worktree`.
  - A repo whose path holds `push` having every `git -C <path>` denied.
- **Recording the agent's pid from the attach line.** That's new state, so don't propose it.
- **Left open, for a reviewer to file as old holes:**
  - `adw_agentsh_policy.py` calls `render_config` itself, so a broken template gives a traceback there. That's the engineer's own command and no agent runs.
  - `agentsh_guard.read` catches only `OSError` on the template, so a template that isn't valid UTF-8 stops the run with a `UnicodeDecodeError` before any agent starts.

## Files and changes

Modules are under `.claude/skills/sssf/templates/adws/adw_modules/`. Don't edit the stamped `adws/adw_modules/`.

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

**The stop walk.** In `_kill_descendants`, change one expression, and its `except` stays:

```python
parent = int((entry / "stat").read_bytes().rsplit(b")", 1)[1].split()[1])
```

`int()` takes bytes, and a name holding `)` still splits on the last one. Add one sentence to the docstring: `stat` is read as bytes because a process's name needn't be valid UTF-8, and a skipped process would take its subtree out of the walk. Change nothing else in the walk or in `stop_tree`. `rules.stop_tree`'s `ps` asks only for pids, so it needs no change.

**The push rule.** In `COMMAND_HEAD`, the double-quoted Python string doubles the backslashes:

```python
    args_patterns: ['^\\S*\\s+push(\\s|$)', '(?s)^\\S*\\s+-.*push']
```

`(?s)` goes on the second pattern only, and neither pattern gets `(?m)`, or a commit message line starting `push` would be denied. Add to the comment above it: `(?s) so a newline in an option's value can't hide the verb.`

**A broken template.** In `check_machine`, replace the single `if not guard.config_template or installed != render_config(...)` block with:

```python
if not guard.config_template:
    raise AgentshRefused(f"{CONFIG} is not the file SSSF renders; {FIX}")
try:
    rendered = render_config(guard.config_template, allowed).encode()
except Exception as error:    # yaml can hand back any shape, or raise several ways, and nothing else runs here
    raise AgentshRefused(f"agentsh_sssf.yaml can't be rendered ({type(error).__name__}); restore the file from "
                         "the SSSF skill, or set `local_guard: text` in adws/adw_sssf_config/repo.yaml") from None
if installed != rendered:
    raise AgentshRefused(f"{CONFIG} is not the file SSSF renders; {FIX}")
```

The message doesn't use `FIX`, because `FIX` sends the engineer to a script that can't render this template either. `render_config` itself doesn't change. `session.ensure` already catches `AgentshRefused` around `check_machine`, so the refusal prints with no traceback.

### `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py`

Ahead of `print("Run these, then start the chain again:\n")`, print this. No printed warning line may start with `sudo`, because the test finds the first sudo line that way:

```python
print("Every agent must be stopped before you run these lines. `sudo install` follows a link, so an agent "
      "running during the install could swap a temp file for a link to a root-only file and get it copied "
      f"to {agentsh_guard.CONFIG} at mode 0644.\n")
```

Add to the module docstring, after the sentence about the `sudo install` lines: `Stop every agent before running them, because sudo install follows a link.`

### `.claude/skills/sssf/references/config.md`

In "Running local agents under agentsh":

- Install step 2 becomes: `Stop every agent, then run those lines. `sudo install` follows a link, so an agent running during the install could swap a temp file for a link to a root-only file and get it copied to `/etc/agentsh/sssf.yaml` at mode 0644. The command prints the same warning.`
- The refusal sentence adds that a config template SSSF can't render also refuses.
- After the paragraph ending with the design doc link, add a list headed `More known limits, each with what still applies:`
  - `GIT_CONFIG_COUNT` with an alias, and `git send-pack`, reach exec without the word `push` where the rule looks. The network rules refuse GitHub.
  - `mv .git .git2`, an edit and a move back, on a run without `--worktree`. The after-call guard undoes it.
  - A repo whose path holds `push` has every `git -C <path>` denied. It fails closed.
  - A stop with the chain out of file descriptors, where neither `ps` nor `/proc` can be read, kills only the wrap. It has no parent-death signal, so the agent can run on until it exits.
  - A signal landing inside the walk can cut it short, and `_stopped` then blocks a second attempt. `rules.stop_tree` doesn't run again for that process, so what the walk missed can run on.

### `docs/designs/agentsh-local-sandbox.md`

In `## What got built` only, extend the push bullet: add `git -c $'a=b\nc' push` and `git -C $'/x\ny' push` to the denied list, and `git commit -m $'line one\npush it'` and `git log --grep $'x\npush'` to the list that runs. Change nothing else in the doc.

### Other docs

No `README.md` section covers this, and there is no CHANGELOG. No `app_docs/` write-up covers this, so the documenter has nothing older to note. `docs/designs/agentsh/sssf-net.yaml` already holds the pattern, and `docs/designs/agentsh/corpus.sh` already holds both git spellings.

## Tests

All in `.claude/skills/sssf/tests/test_agentsh_guard.py`. The config comes from the shipped `sssf.config.yaml` as the existing tests do, and nothing needs the service.

**The stop walk.** New test `test_a_stop_takes_a_child_with_a_non_utf8_name_and_its_own_child(machine, service, tmp_path, monkeypatch)`, with `FAKE_WRAP=fork` and `rules.subprocess` left alone, so `ps` works:

- Write an executable at `os.fsencode(tmp_path) + b"/\xff"`:
  ```sh
  #!/bin/sh
  sh -c 'sleep 2; : > "$1"' grandchild "$2" &
  echo $$ > "$1.pid"
  sleep 2
  : > "$1"
  ```
  Pass it two marker paths: `$1` for itself and `$2` for its own child. Make them `tmp_path / "child"` and `tmp_path / "grandchild"`, and read the pid from `tmp_path / "child.pid"`.
- The agent is `[sys.executable, "-c", f"import subprocess, time; subprocess.Popen([{bad!r}, {child!r}, {grandchild!r}]); time.sleep(30)"]`, where `bad` is the bytes path. Start it with `launch(machine, "pi", cmd=...)`.
- Wait up to 10 s for the pid file to hold a pid, so the stop can't arrive first. Then assert `b"(\xff)"` is in `Path(f"/proc/{pid}/stat").read_bytes()`, or the test proves nothing. This was confirmed on this kernel: a script's name shows raw in `stat`, and `read_text` raises `UnicodeDecodeError`.
- `process.stop_tree()`, `time.sleep(3)`, then assert neither marker exists.

The test must fail with `read_text` back in place: under text the child is skipped, the agent dies and the child is orphaned before `ps` looks. Check that once by hand while building, and say in your notes that you did.

**The push cases.** Add to the existing lists, which already use `push_patterns()` from the generated policy and the joined form:

- `test_push_cases_that_are_denied`: `["git", "-c", "a=b\nc", "push"]` and `["git", "-C", "/x\ny", "push"]`.
- `test_push_cases_that_run`: `["git", "commit", "-m", "line one\npush it"]` and `["git", "log", "--grep", "x\npush"]`.

The other cases in those lists stay. Add `test_push_patterns_match_the_design_policy`, with `skipif` on `docs/designs/agentsh/sssf-net.yaml` missing, as `DENY_POLICY` does: both harnesses' `push_patterns` equal the `sssf-no-push` rule's `args_patterns` in that file. `push_patterns` takes the harness already.

**A broken template.** New parametrized test on `"# only a comment\n"`, `"policies: 5\n"`, `"- a\n"`, `"[1, 2\n"`, `"a: 1\n"` and `"a: 2001-13-45\n"`:

```python
guard = machine.guard.model_copy(update={"config_template": template})
with pytest.raises(agentsh_guard.AgentshRefused, match="agentsh_sssf.yaml"):
    agentsh_guard.check_machine(guard)
```

The last two cover `KeyError` and `ValueError`. Add one case for an empty template, which refuses with `not the file SSSF renders`. And run one broken template through `launch` with a marker command, as `test_each_machine_refusal_fires_before_an_agent_starts` does, asserting the marker never appears.

**The install warning.** In `test_install_script_writes_nothing_under_the_repo_and_agrees_with_the_check`, read `capsys.readouterr().out.splitlines()` once inside `install_and_check`. Take the index of the first line starting `sudo `, and assert that the lines before it contain `Every agent must be stopped` and `follows a link`. The existing `steps` filter on lines starting `sudo install` keeps working on the same list.

**Earlier tests.** They all stay and must pass. The baseline here is 126 passed and 1 skipped in this file, before any of the changes above.

## Verification

- The repo's own test command passes on a machine without the service. It runs `pytest tests` in `.claude/skills/sssf`, which collects `.claude/skills/sssf/tests/test_agentsh_guard.py`, so no extra check is needed.
- A search of `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` finds `read_bytes().rsplit(b")"` in `_kill_descendants`.
- `git diff --stat` touches only `agentsh_guard.py`, `adw_agentsh_policy.py`, `config.md`, the test file and the design doc, plus this spec.
- The live checks under `AGENTSH_LIVE` aren't run. Rollout step 2 runs them. The `(?s)` pattern was tested on the fork's engine, per the design doc, and isn't rerun here.

## For the engineer

These are protected, so the builder must not touch them. They're still open from the revised spec:

1. `adws/adw_guard_change.py`: add `agentsh_guard.py` to `GUARD_MODULES`.
2. `CLAUDE.md`: add `agentsh_guard.py` to the guard code list.
3. `adws/tests/test_guard_change.py`: add a matching assertion if you want one pinned.
