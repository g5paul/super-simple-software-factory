# Refuse an agent's signals to processes outside its session

Item 9 of the Next list in `docs/designs/agentsh-local-sandbox.md` (section "Stock policies, sockets, signals and DNS, 2026-11-10", and "The tenth and eleventh patches: DNS and signals").

## Summary

Four edits in the skill's template guard and two docs:

1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`: `sandbox.ptrace.trace.signal: false` becomes `true`.
2. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`: `policy_text` appends a `signal_rules:` section to every policy, for both harnesses. Signals to `self` and `session` are allowed. Signals to `parent`, `external` and `system` are refused with a message.
3. The same file: `PINNED_SHA256` moves to build `472d63cf`.
4. `.claude/skills/sssf/references/config.md` and `docs/guide/install.md` name the new build and say why it's needed.

Plus unit tests in `.claude/skills/sssf/tests/test_agentsh_guard.py`.

The build the engineer installed (`agentsh --version` prints `agentsh 0.20.6-dev.fork+472d63cf` on this machine):

| | |
|---|---|
| Commit | `472d63cf` on `sssf/dns-signals`, over `3a36c68f` |
| Version | `0.20.6-dev.fork+472d63cf` |
| sha256 | `f9da98024093f13634f26d349237eb217dc5b10fab826d222f08d8a88f79933e` |

## Threat model

The agent runs as the engineer's own user, inside `agentsh wrap`. The tracer runs as user `agentsh`, and the service judges the agent's execs, opens, connects and, after this change, signals. At the baseline, signal syscalls aren't stopped (`trace.signal: false`), and the pinned build's ptrace handler allowed every signal anyway. So an agent can `kill` any process the engineer's user owns. The design doc's probes showed it killing two decoys outside its session with TERM and KILL. That includes the SSSF runner, which is the agent's real parent and sits outside the session. Killing the runner ends the run before its after-call checks, the ones that put back a renamed `.git` and catch a swapped workspace folder. The agent can also kill the engineer's editor, other Claude sessions and other runs. A second route, which the fork closes too, is `fcntl(F_SETOWN)` with `F_SETSIG` and `O_ASYNC`: the kernel itself signals the owner.

What this guard can't stop at its level:

- The tracer checks before the kernel delivers. A target that exits, with its pid reused in between, can still be reached if the engineer's user may signal the new process. The window is a few `/proc` reads (design doc).
- A process that escaped the wrap altogether (an old hole: "a process that detaches from the run") isn't traced, so its signals aren't judged.
- Anything outside the wrap: quality checks, `worktree_setup`, host git.
- The agent still can't free itself from the tracer, which runs as `agentsh`, or `ptrace` a process (Yama `ptrace_scope` 1). That holds at the baseline too.

## Baseline

`main` at `c736b1a` ("Put the hub on the text guard for the signals run"). Every finding is judged against it. At the baseline:

- `agentsh_sssf.yaml` has `trace.signal: false`.
- `policy_text` writes `version`, `name`, `description`, `command_rules`, `file_rules` and `network_rules`, and no `signal_rules`.
- `PINNED_SHA256` is `0e4430b5…40886`, build `3a36c68f` on `sssf/races`.
- The hub's own `adws/adw_modules/agentsh_guard.py` and `agentsh_sssf.yaml` are byte-identical to the template copies.

## Bar

The change must:

- refuse an agent's signal to a process outside its session (`parent` outside the session, `external`, `system`), whichever of the six syscalls sends it, and refuse making such a process a file's signal owner. The fork does the judging. The guard has to turn tracing on and write the rules.
- keep signals inside the session working: shell job control, `timeout`, `kill $!`, `kill %1`, a test runner stopping its workers, Claude Code and pi stopping their own tools, and glibc's real-time signals 32 and 33 between threads. A policy that breaks a run is a finding.
- name every signal in each rule as `@all` plus `'32'` to `'63'`. agentsh's `@all` is 1 to 31, and agentsh refuses to compile a rule naming 64 ("signal number out of range: 64"), so 64 can't be named and stays refused by the engine's default deny.
- put the allow rules before the deny rules, since the engine takes the first rule that matches.
- set `PINNED_SHA256` to exactly `f9da98024093f13634f26d349237eb217dc5b10fab826d222f08d8a88f79933e`, with a comment naming build `472d63cf` on `sssf/dns-signals`.
- change nothing else in the guard: no other line of `agentsh_sssf.yaml`, no change to `check_machine`, `file_rules`, the command or network rules, denial counting, or anything else in `agentsh_guard.py` beyond the pin line, the new constants and function, and the one `policy_text` return line. Any change beyond these five is a finding.
- promise nothing in `config.md` that the probes and the fork's tests didn't show.

It must never, where the baseline does otherwise: keep an agent file, run agent code, or delete or overwrite an engineer file. The earlier bars stand: every existing file, command and network rule keeps its text and order.

## Out of scope

These are old holes or other work. File them as old holes, not findings against this change:

- The fork's code, which was reviewed and tested outside this chain.
- `resource_limits`, `env_policy`, `package_rules` and `unix_socket_rules`. Default-deny already refuses socket connects.
- The pin check's two old holes (Next item 1): an edit to the project's `agentsh_guard.py`, and a service still running an older binary.
- The tool-config task (uv's config lookup, Corepack's `package.json` lookup).
- pi's policy: it gets the same `signal_rules` as Claude Code's, and nothing else about it changes.
- The check-before-delivery pid-reuse window, and signal 64, which agentsh can't name (above).
- A refused signal is traced, not counted. `_take` in `agentsh_guard.py` counts only named `ptrace_execve` denials. A `ptrace_signal` denial falls into `other_denials`, like file and network denials. This change doesn't make signal refusals count toward the three-refusal stop.
- The hub's own `adws/adw_modules/` copies, which are protected. See "For the engineer".
- `docs/designs/agentsh-local-sandbox.md` and `docs/designs/agentsh/`, which record evidence as it was gathered. The hub session records the post-merge probes there. Don't edit them.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`

Under `sandbox.ptrace.trace`, change `      signal: false` to `      signal: true`. Change no other line, and add no comment.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

**a. The pin, line 40.** Replace

```python
PINNED_SHA256 = "0e4430b5eb9f77ddb8014bcc6cd1fad3c8eda6a4deeb7b041777f74c46440886"   # build 3a36c68f on sssf/races
```

with

```python
PINNED_SHA256 = "f9da98024093f13634f26d349237eb217dc5b10fab826d222f08d8a88f79933e"   # build 472d63cf on sssf/dns-signals
```

**b. Constants.** Right after `NETWORK_TAIL = ...` (the line `NETWORK_TAIL = "  # Everything else is denied: no match is a deny.\n"`), add:

```python
# agentsh's @all is signals 1 to 31, and a signal no rule names is refused, so the real-time
# signals are named too: glibc uses 32 and 33 between a program's own threads. agentsh
# can't name 64.
EVERY_SIGNAL = ["@all"] + [str(n) for n in range(32, 64)]
SIGNAL_REFUSAL = "SSSF: an agent may signal only its own processes"
```

**c. The rules.** Add this function right after `file_rules` (before `policy_name`):

```python
def signal_rules() -> str:
    """Signals to the agent's own processes, and nothing else. The first rule that matches
    decides, so the allows come first: a process may signal its parent when the parent is
    in the session too. The SSSF runner, the agent's real parent, is outside it: `external`."""
    signals = str(EVERY_SIGNAL)          # ['@all', '32', ...]: quoted strings, as agentsh reads them
    lines = ["signal_rules:"]
    for target, decision in (("self", "allow"), ("session", "allow"),
                             ("parent", "deny"), ("external", "deny"), ("system", "deny")):
        lines += [f"  - name: sssf-signal-{target}", f"    signals: {signals}",
                  "    target:", f"      type: {target}", f"    decision: {decision}"]
        if decision == "deny":
            lines.append(f"    message: '{_q(SIGNAL_REFUSAL)}'")
    return "\n".join(lines) + "\n"
```

The rule names are `sssf-signal-self`, `sssf-signal-session`, `sssf-signal-parent`, `sssf-signal-external` and `sssf-signal-system`. The numbers must be quoted strings: agentsh's `signals` field is a list of strings.

**d. `policy_text`.** Its return ends `...{network}")`. Make it `...{network}{signal_rules()}")`, so `signal_rules:` comes last, after `network_rules`. Nothing else in `policy_text` changes. Putting it last keeps `test_file_rules_equal_the_designs_deny_policy_output` valid, since that test slices from `file_rules:` to `network_rules:`.

Don't touch any other line: no docstring, no message, no `check_machine` line.

### 3. `.claude/skills/sssf/references/config.md`, section "Running local agents under agentsh — `local_guard`"

**a. Opening paragraph** (the one starting "It's opt-in, and tested so far only on Python repos"). Replace the sentence that starts "It needs agentsh `0.20.6-dev.fork+3a36c68f`" and ends "then read it there." with:

> It needs agentsh `0.20.6-dev.fork+472d63cf`, the fork's commit `472d63cf` on `sssf/dns-signals`, which is the build SSSF pins: earlier builds fail `tar -x` on files in subfolders, let an agent read any file it could read outside the wrap through a namespace of its own, let it move a file it may not read into its workspace with `mv` and read it there, let it kill any process your user owns, the SSSF runner among them, and let a DNS query carry data to a server the agent picked. After an SSSF update, reinstall with `just agentsh-install` and run the sudo lines it prints: until you do, a run refuses, because the installed config and policies differ from what the checkout renders.

Leave the rest of the paragraph ("A pi roster has no policy yet. Keep `local_guard: text`…") as it is.

**b. "More known limits" list.** Add one bullet right after the bullet that starts "Under the wrap, a tool that needs a namespace gets `EPERM`":

> - An agent may signal only processes in its own session. agentsh checks a signal before the kernel delivers it, so if the target exits and its pid is reused in between, the new process can be reached when your user may signal it. agentsh can't name signal 64 in a rule, so a program's signal 64, even to its own processes, is refused. A refused signal goes to the trace as a denial and doesn't count toward the three-refusal stop.

Don't change any other bullet or paragraph. Keep the wording as close as you can. It claims only what the design doc and the fork's tests show. Don't add a claim that a named tool works under the new rules: the hub session probes that after the merge.

### 4. `docs/guide/install.md`

- Line 47, the pinned-versions row, becomes:
  `| The agentsh build | sha256 `f9da9802…9933e`, commit `472d63cf` on the fork | `PINNED_SHA256` in `agentsh_guard.py` |`
- Line 33, the agentsh row in the tools table, names the same build, so it goes stale too: change `0.20.6-dev.fork+3a36c68f` to `0.20.6-dev.fork+472d63cf` and leave the rest of the row as it is.

### 5. Tests: `.claude/skills/sssf/tests/test_agentsh_guard.py`

Add these to the "generated policies" block, after `test_a_quote_in_a_value_stays_one_scalar`. They use the module's existing `PATHS`, `TEMPLATE` and `yaml` imports. Neither needs a roster config. A test you add that does need one must use the `cfg` fixture, which builds from the shipped `templates/sssf.config.yaml`.

```python
@pytest.mark.parametrize("harness", ["claude_code", "pi"])
def test_every_policy_lets_an_agent_signal_only_its_own_processes(harness):
    settings = repo_settings.RepoSettings(
        secret_files=["*.pem"], deny_exec=[{"name": "no-rm", "command": "rm", "reason": "x"}],
        network_allow=["a.example.com"])
    text = agentsh_guard.policy_text(settings, "/m/proj/.git", harness, "sssf-proj-x", **PATHS)
    rules_ = yaml.safe_load(text)["signal_rules"]
    every = ["@all"] + [str(n) for n in range(32, 64)]
    assert [(r["target"]["type"], r["decision"]) for r in rules_] == [
        ("self", "allow"), ("session", "allow"),
        ("parent", "deny"), ("external", "deny"), ("system", "deny")]
    assert all(r["signals"] == every for r in rules_)
    for r in rules_:
        assert ("message" in r) == (r["decision"] == "deny")
        if "message" in r:
            assert "only its own processes" in r["message"]
    assert text.index("network_rules:") < text.index("signal_rules:")


def test_the_server_config_traces_signals():
    config = yaml.safe_load(agentsh_guard.render_config(TEMPLATE.read_text(), ["sssf-x-claude"]))
    assert config["sandbox"]["ptrace"]["trace"] == {
        "execve": True, "file": True, "network": True, "signal": True}
```

Don't change an existing test. The suite monkeypatches `PINNED_SHA256`, so no test sees the real value. Don't add one that compares it with `/usr/bin/agentsh`.

### Docs this change makes out of date

Steps 3 and 4 cover them: `config.md` (the build sentence, the reinstall, the new limit) and `docs/guide/install.md` (the pin row and the version row). There's no CHANGELOG, and no help text names the build. The new function's docstring is part of step 2. `docs/concepts.md` says nothing about signals or the build, so it doesn't change. Nothing in `app_docs/` is edited. For the documenter: an earlier write-up that pins `3a36c68f`, or says agentsh doesn't judge signals, now describes the old behaviour. Note that it's been replaced.

## Verification

Run from the repo root:

1. `grep -qxF 'PINNED_SHA256 = "f9da98024093f13634f26d349237eb217dc5b10fab826d222f08d8a88f79933e"   # build 472d63cf on sssf/dns-signals' .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` exits 0.
2. `grep -qxF '      signal: true' .claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml` exits 0.
3. agentsh compiles both rendered policies, `signal_rules` included. `agentsh policy validate` parses and compiles a file, and it fails on a bad signal or target:
   ```
   d=$(mktemp -d) && uv run --directory .claude/skills/sssf/templates/adws --with pydantic --with pyyaml python -c "import sys; from adw_modules import agentsh_guard as g, repo_settings as r; [open(f'{sys.argv[1]}/{h}.yaml', 'w').write(g.policy_text(r.RepoSettings(), '/m/p/.git', h, 'sssf-p-' + h, sessions='/m/s', refuse_script='/m/r.py')) for h in ('claude_code', 'pi')]" "$d" && agentsh policy validate "$d/claude_code.yaml" && agentsh policy validate "$d/pi.yaml" && grep -q '^signal_rules:' "$d/claude_code.yaml" && grep -q '^signal_rules:' "$d/pi.yaml"
   ```
   exits 0 and prints `ok` twice.
4. For you and the reviewer to read, not an automated check: `git diff -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml` shows one line changed, and `git diff -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows only the pin line, the two constants, `signal_rules` and the `policy_text` return line.
5. `grep -n -E "3a36c68f|0e4430b5" .claude/skills/sssf/references/config.md docs/guide/install.md .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` finds nothing (exit 1).
6. The repo's quality checks pass, including the skill suite as `adws/adw_modules/quality.py` runs it (`uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`), with `test_agentsh_guard.py`'s two new tests.

After the merge and the engineer's reinstall, the hub session runs the live probes on the installed build. These aren't the builder's job:

- Decoys outside the session survive TERM and KILL from each of `kill`, `tkill`, `tgkill`, `rt_sigqueueinfo`, `rt_tgsigqueueinfo` and `pidfd_send_signal`.
- `F_SETOWN` with `F_SETSIG` set to SIGTERM fails.
- `sleep 9 & kill $!`, `timeout 1 sleep 5` and a background job's `kill %1` work.
- The check set holds as before.
- Claude Code and pi exit 0 under the wrap.
- A guard run passes under `local_guard: agentsh`.

## For the engineer

These paths are protected, so the builder doesn't touch them:

- `adws/adw_modules/agentsh_guard.py` and `adws/adw_modules/agentsh_sssf.yaml`, the hub's own install, still pin `0e4430b5…` with `trace.signal: false`. Bring the change in with the hub's SSSF update after this merges (`just sssf-update`), which also moves `adws/adw_sssf_config/stamp.json`. Then reinstall with `just agentsh-install` and its sudo lines, since the rendered config and policies change.
- Set the hub's `local_guard` back to `agentsh` in `adws/adw_sssf_config/repo.yaml` after the update and reinstall, then run the real `check_machine` and the probes above.
- One risk to watch in the probes: a signal from Claude Code or pi to its wrap client or to the SSSF runner, if either sends one, is now refused as `external`. The fork's tests didn't cover the real harnesses, so "Claude Code and pi exit 0 under the wrap" is the probe that settles it.
