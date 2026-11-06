# Ask for the obs relay from a pi agent's VM env, and turn obs off with `OBS_DISABLE=true`

## Summary

Decisions 37 to 50 of `docs/designs/remote-sandboxes.md` add an obs relay for
pi agents in a VM. The relay (`obs_relay.py`, `sssf_obs_relay.cjs`) and the VM
wiring (`Remote.relay_obs`, `Remote.start` in `sandbox_remote.py`) are already
built on this branch. The guard part is still missing: decision 40 and 46 say
`agent_pi._in_vm` decides which value turns obs off, which token stays behind,
and when to ask for a relay.

Today `_in_vm` (`.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py:552`)
sets `OBS_DISABLE=1` and pops `OBS_AUTH_TOKEN` for every pi call in a VM. The
pi-observability extension turns off only on `OBS_DISABLE === "true"`, so `1`
left obs on. Every VM run has posted, without a token, to a VM address where
nothing listened. Nothing asks for a relay.

This change, on a remote tree only:
1. still pops `OBS_AUTH_TOKEN`, and keeps its value only for an `obs_relay.Target`;
2. sets `OBS_DISABLE=true` in place of `1`;
3. calls `tree.relay_obs(obs_relay.Target(...))` when the call loads at least
   one roster extension (not counting the refuse extension), the env had a
   non-empty `OBS_AUTH_TOKEN`, and the operator's `OBS_DISABLE` wasn't `true`;
4. has `run` pass `request.extensions` to `_in_vm`.

`Remote.start` already sets the relay's loopback `OBS_SERVER_URL` and drops
`OBS_DISABLE` when the relay comes up. If the relay doesn't come up, it leaves
`OBS_DISABLE=true` in place. That part needs no change.

## Threat model

- The agent runs as the engineer's own user. On the host it can read `.env`,
  the process env and `~/.pi`. This guard can't stop that, and isn't meant to.
  In a VM run, the job is narrower: the obs server's token must never reach
  the VM, and obs must not post from the VM except through SSSF's relay.
- In the VM, the agent controls every process there. It can read its own env
  and argv, read files anywhere in the VM (the relay's file in `/tmp`
  included), write anything into the relay's file, post anything to the
  relay's port, kill the relay, and edit a committed extension for the next
  call. Whatever reaches the VM's env, argv or files, it can copy into the
  clone, and `commits_out` brings that back to the host branch.
- At this level, the guard chooses three things: the env the agent starts
  with, the token that stays on the host, and whether the host relays this
  call's events. It can't stop the agent from sending forged events to the
  relay. The host forwarder bounds those (well-formed only, the call's own
  pool, name and tags, the caps), and that code isn't a guard file. It can't
  stop an agent in the VM from reaching the open internet either (decision 8).

## Baseline

`main` at commit `f2bf0bd` ("Bring fork.md and the README's open list up to
date"). Judge every finding against `f2bf0bd`. The relay commits on this
branch (`244fc71`, `39d7970`, `d1de797`) are not guard files and aren't the
baseline for this guard change.

At `f2bf0bd`:
- `agent_pi._in_vm(env, tree, refuse_rules)` returns `env` unchanged on
  `sandbox.LOCAL`. Otherwise it sets `OBS_DISABLE="1"`, pops `OBS_AUTH_TOKEN`,
  sets `SSSF_PI_TOKEN=vm_token()`, and installs `REFUSE_EXTENSION` when there
  are refuse rules.
- `run` calls `_in_vm(env, tree, request.refuse_rules)`, then `_in_container`.
- `sandbox_remote.Remote._env` drops every `HELD` name (`OBS_AUTH_TOKEN`) for
  every VM command.
- No pi agent in a VM gets a relay.

## Bar

The change must:
- Keep `OBS_AUTH_TOKEN` out of the env `_in_vm` returns, in every case,
  relay or not. Its value goes only into `obs_relay.Target.token`, and that
  Target goes only to `tree.relay_obs`. No value of the token may reach any
  env, argv, file or trace event the VM or the agent sees.
- Set `OBS_DISABLE="true"` in the env `_in_vm` returns on a remote tree,
  whether or not it asks for a relay.
- Ask for a relay (call `tree.relay_obs` exactly once) only when all three
  conditions hold: a roster extension other than the refuse extension, a
  non-empty `OBS_AUTH_TOKEN` in the env, and the env's `OBS_DISABLE` not
  equal to `"true"`. Otherwise, don't call it.
- Build the Target from the env `child_env` built: `server` from
  `OBS_SERVER_URL`, or `obs_relay.DEFAULT_SERVER` when it's unset or empty;
  `name` from `OBS_NAME`; `pool` from `OBS_POOL`; and `tags` from `OBS_TAG`,
  split on `,`, trimmed, with empty entries dropped.
- Leave these exactly as at baseline: a `sandbox.LOCAL` run (host and
  operator container), whose env comes back identical and gets no
  `relay_obs` call; a Claude Code roster (`agent_cc` is untouched);
  `Remote._env` and `HELD`; the refuse extension's install and its place
  last in argv; and the `SSSF_PI_TOKEN` → `OPENAI_API_KEY` path.

The change must never, where the baseline does otherwise:
- keep an agent file, run agent code, or delete or overwrite an engineer file.
  The change only edits a dict and makes one in-memory call.
- pass `OBS_AUTH_TOKEN`, or any new host variable, into a VM.
- ask for a relay on a local tree, or when the operator turned obs off with
  `OBS_DISABLE=true`.

## Out of scope

These are old holes, or holes in code outside the guard files, that a
reviewer should file and not count against this change:
- **Forged events.** An agent in the VM can post any well-formed event to the
  relay or write it to the relay's file, within the caps. The forwarder
  overwrites pool, name and tags, but `session_id`, `seq` and payloads are the
  agent's (decision 38).
- **Which extension is obs.** SSSF doesn't tell (decision 40). Any roster
  extension makes the call relayed, even one that isn't obs.
- **Operator `OBS_DISABLE` values other than `true`.** The operator's `1`,
  `yes` and so on don't count as off, because the extension reads only
  `true`. A relay is asked for, as a host run would post.
- **A pending request that outlives a failed start.** `Remote.start` takes the
  request after `_refresh` and `handoff_in`. If either raises, the request
  stays for the next start. That's in `sandbox_remote.py`, not a guard file,
  and a failed start stops the run anyway.
- **Other secrets under `OBS_`/`SSSF_` names**, the Claude and ChatGPT tokens
  in the VM, and agent-edited extensions: as `references/config.md` already
  lists under the known limits.
- **The `sandbox.Sandbox` Protocol** (`sandbox.py`) doesn't declare
  `relay_obs`. Only remote trees reach the call, and they all subclass
  `Remote`. Leave the Protocol alone.
- The repo's installed copy under `adws/adw_modules/` is protected and stays
  behind. Only the skill's template changes.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Import: `from . import obs_relay, rules, sandbox, silence`.
- Add a helper above `_in_vm`:

  ```python
  def _obs_target(env: dict[str, str], token: str, extensions) -> Optional[obs_relay.Target]:
      """Where a VM call's obs events go, or None when obs couldn't post
      (design decision 40): no roster extension, no OBS_AUTH_TOKEN on the
      host, or the operator turned obs off. SSSF doesn't try to tell which
      extension is obs."""
      roster = [e for e in extensions if str(e) != str(REFUSE_EXTENSION)]
      if not roster or not token or env.get("OBS_DISABLE") == "true":
          return None
      return obs_relay.Target(
          server=env.get("OBS_SERVER_URL") or obs_relay.DEFAULT_SERVER,
          token=token,
          name=env.get("OBS_NAME", ""),
          pool=env.get("OBS_POOL", ""),
          tags=[t.strip() for t in env.get("OBS_TAG", "").split(",") if t.strip()])
  ```

- Replace `_in_vm` with the following. The signature gains `extensions=()`
  last, so existing three-argument calls still work:

  ```python
  def _in_vm(env: dict[str, str], tree, refuse_rules: list[dict],
             extensions=()) -> dict[str, str]:
      """On a remote tree: the ChatGPT token, renamed to OPENAI_API_KEY in the VM
      (sandbox_remote), SSSF's refuse extension copied in, and obs off with the
      value the extension reads, OBS_DISABLE=true. The obs server's token stays
      on the host. When obs could post (_obs_target), the tree gets a relay for
      this call. Remote.start then points OBS_SERVER_URL at it and drops
      OBS_DISABLE, and the host posts the events with the token (design
      decisions 37 to 50). In the operator container, the token comes through a
      socket instead (_in_container)."""
      if tree is sandbox.LOCAL:
          return env
      token = env.pop("OBS_AUTH_TOKEN", "")   # never enters the VM; only the host relay holds it
      target = _obs_target(env, token, extensions)
      env["OBS_DISABLE"] = "true"             # the only value the obs extension reads as off
      env["SSSF_PI_TOKEN"] = vm_token()
      if refuse_rules:
          tree.install(REFUSE_EXTENSION)
      if target is not None:
          tree.relay_obs(target)              # for the next start only: run's own
      return env
  ```

  Read the target before setting `OBS_DISABLE`, so the operator's value is
  the one checked. Call `relay_obs` last, after `vm_token()` and `install`, so
  a failure in either leaves no pending request behind.

- In `run`, change the call to
  `env = _in_vm(env, tree, request.refuse_rules, request.extensions)`.
  Leave everything else in `run` as it is, including the order of `-e`
  flags.

### 2. `.claude/skills/sssf/tests/test_rules.py`

- `test_a_pi_agent_in_a_vm_gets_a_token_and_the_refuse_extension` (around
  line 426): change both `"OBS_DISABLE": "1"` / `== "1"` expectations to
  `"true"`. Keep the rest. With no extensions passed, the fake `vm` needs no
  `relay_obs`.
- The run-level test that ends around line 301 (the stand-in `vm =
  SimpleNamespace(install=..., start=start, name="vercel")`, with
  `extensions=["first.ts", "second.ts"]` and `OBS_AUTH_TOKEN=secret`): now it
  asks for a relay. Add `relay_obs=asked.append` to the namespace, with
  `asked = []`. Also `monkeypatch.delenv` `OBS_DISABLE`, `OBS_SERVER_URL`,
  `OBS_TAG` and `OBS_POOL` before `run`, so the shell's env can't change the
  result. Change the last assert to `got["env"]["OBS_DISABLE"] == "true" and
  "OBS_AUTH_TOKEN" not in got["env"] and "secret" not in json.dumps(got)`,
  and add `assert [t.token for t in asked] == ["secret"]`. Keep the argv
  order assert (`loaded(got["command"]) == want`), which pins the refuse
  extension last.

### 3. `.claude/skills/sssf/tests/test_sandbox_remote.py`

- `test_a_pi_agents_obs_token_stays_on_the_host_and_obs_is_disabled` (around
  line 652): expect `passed["OBS_DISABLE"] == "true"`. Also assert
  `box._obs_target is None`, since the test passes no extensions.
- Add, in the obs relay section after `relaying` and `_poster`:
  - `test_a_pi_call_with_an_extension_and_the_obs_token_is_relayed_without_the_token(relaying, monkeypatch)`.
    Patch `agent_pi.vm_token` to `"eyJ-access"`. Call
    `env = agent_pi._in_vm({"OBS_AUTH_TOKEN": "obs-secret", "OBS_SERVER_URL": obs.url, "OBS_NAME": "builder", "OBS_POOL": "sssf-abc", "OBS_TAG": "sssf,adw-abc", "SSSF_ADW_ID": "abc"}, box, [], ["obs.ts"])`.
    Assert that `box._obs_target.token == "obs-secret"` and
    `box._obs_target.server == obs.url`. Then
    `box.start(["sh", "-c", "env | grep ^OBS_ || true"], env=env)` and read
    it. Assert that the output matches
    `^OBS_SERVER_URL=http://127\.0\.0\.1:\d+$`, that it has no
    `OBS_DISABLE`, and that `"obs-secret"` is in no `json.dumps(c)` for
    `c in box.raw.calls` and nowhere in `json.dumps(box.events)`.
  - `test_a_pi_call_that_obs_couldnt_post_from_gets_obs_disable_true_and_no_relay(box, monkeypatch)`.
    Use the same `_in_vm` call with `extensions=[]`, then `box.start(["sh",
    "-c", "env | grep ^OBS_ || true"], env=env)`. Assert that
    `OBS_DISABLE=true` is in the output, that `"obs-secret"` is in neither
    the output nor any raw call, and that there's no `obs_relay` event in
    `box.events`.

### 4. `.claude/skills/sssf/tests/test_agent_pi.py`

Add unit tests near the `_in_container` test (line ~206). Use a fake tree
`vm = SimpleNamespace(install=lambda p: None, relay_obs=asked.append)`, add
`from types import SimpleNamespace` if needed, and patch `agent_pi.vm_token`
to return `"eyJ-access"`. A base env:
`{"OBS_AUTH_TOKEN": "obs", "OBS_NAME": "builder", "OBS_POOL": "sssf-abc", "OBS_TAG": " sssf, ,adw-abc ,"}`.

- `test_a_vm_call_with_an_extension_and_the_obs_token_asks_for_a_relay`:
  `_in_vm(dict(base), vm, [], ["obs.ts"])`. Assert one Target in `asked`,
  with `token == "obs"`, `server == obs_relay.DEFAULT_SERVER`,
  `name == "builder"`, `pool == "sssf-abc"`, `tags == ["sssf", "adw-abc"]`.
  Assert that the returned env has `OBS_DISABLE == "true"` and no
  `OBS_AUTH_TOKEN`, that `"obs"` isn't among its values, and that
  `"obs" not in repr(asked[0])`, which pins `repr=False`.
- `test_the_relay_posts_to_the_hosts_obs_server_url`: parametrize the
  `OBS_SERVER_URL` value, `"http://host:9"` → `"http://host:9"` and
  `""` → `obs_relay.DEFAULT_SERVER`.
- `test_no_relay_is_asked_for_when_obs_couldnt_post`, parametrized:
  - `extensions=[]`;
  - `extensions=[str(agent_pi.REFUSE_EXTENSION)]` (the refuse extension alone);
  - `OBS_AUTH_TOKEN` absent;
  - `OBS_AUTH_TOKEN=""`;
  - `OBS_DISABLE="true"`.

  Each one asserts `asked == []`, `env["OBS_DISABLE"] == "true"` and
  `"OBS_AUTH_TOKEN" not in env`.
- `test_an_operator_obs_disable_other_than_true_still_relays`: with
  `OBS_DISABLE="1"` in the base, a relay is asked for and the returned env's
  `OBS_DISABLE == "true"`.
- `test_a_local_tree_never_asks_for_a_relay`: patch `sandbox.current`?
  Not needed. Call `_in_vm(dict(base), sandbox.LOCAL, [], ["obs.ts"])` and
  assert it returns `base` unchanged (token kept, no `OBS_DISABLE`).
  `sandbox.LOCAL` has no `relay_obs`, so a call would raise.
- `test_run_passes_the_calls_extensions_to_in_vm`: use the existing
  `_fake_pi` or a stub. Monkeypatch `agent_pi._in_vm` to record its
  arguments and return the env, and `sandbox.current` to `sandbox.LOCAL`.
  Monkeypatch `resolve_model` and `context_window` as the test_rules
  run-level test does. Call `run` with
  `request.extensions=["a.ts"]` (catch `RuntimeError` if the stub prints no
  turn) and assert that the recorded fourth argument is `["a.ts"]`.

Build any `AgentRequest` the way the file already does. No test-only roster
config is needed. If one is, load the shipped
`.claude/skills/sssf/templates/sssf.config.yaml` through `_shipped`.

### 5. `.claude/skills/sssf/references/config.md`

In "### pi extensions in a VM" (around line 425), replace the sentence
"Pi agents also get `OBS_DISABLE=1`, so an obs extension loads but posts
nothing. SSSF's trace still records the run." with the following:

> A pi agent's obs events go through SSSF's relay to the host's obs server
> (`OBS_SERVER_URL`, or `http://127.0.0.1:43190`) when the agent loads at
> least one `harness_engineering` extension, the host has `OBS_AUTH_TOKEN`,
> and `OBS_DISABLE` isn't `true`. The relay listens on the VM's loopback, and
> the host posts the events with its own token, so the token never enters
> the VM. The host sets each event's pool, agent name and tags to the call's
> own. Otherwise obs is off: the agent gets `OBS_DISABLE=true`. If the relay
> won't start, obs is off for that call, with one warning in SSSF's trace.
> SSSF's trace records the run either way, with one `obs_relay` event per
> relayed call.

Keep the sentences before and after it as they are.

### Docs this makes out of date

- `references/config.md`, as above.
- The docstring of `_in_vm`, rewritten above.
- No CHANGELOG in the repo. The README doesn't describe VM obs.
  `docs/designs/remote-sandboxes.md` already states decisions 37 to 50, so
  leave it.
- For the documenter: no `app_docs/` directory exists in this repo, so no
  earlier write-up goes stale. The `d8cdd2e9_vm-obs-token` spec and the
  `a761dcc0` specs describe `OBS_DISABLE=1`. They are records, not docs, and
  stay as they are.

## Verification

Run from the repo root. Put `env -u SSSF_REFUSE_RULES -u CLAUDE_CODE_SHELL_PREFIX -u OBS_AUTH_TOKEN -u OBS_DISABLE -u OBS_SERVER_URL`
in front, so an agent's shell env can't skew env-copying tests.
`test_an_agent_gets_sssfs_shell_prefix_only_with_refuse_rules` is known to
fail when the first two are set, on the baseline as well.

1. New and changed tests:
   `cd .claude/skills/sssf && env -u SSSF_REFUSE_RULES -u CLAUDE_CODE_SHELL_PREFIX -u OBS_AUTH_TOKEN -u OBS_DISABLE -u OBS_SERVER_URL uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_pi.py tests/test_rules.py tests/test_sandbox_remote.py tests/test_obs_relay.py -q -p no:cacheprovider`
   must exit 0. The relay end-to-end test needs `node`, which the VM tool
   check already requires. It skips without it.
2. Test-first check: with `_in_vm` temporarily back at baseline, which sets
   `OBS_DISABLE="1"`, takes no extensions and makes no `relay_obs` call, the
   new `test_agent_pi.py` relay tests and the
   `OBS_DISABLE == "true"` asserts must fail. Restore the change afterwards.
3. The whole skill suite still passes:
   `cd .claude/skills/sssf && env -u SSSF_REFUSE_RULES -u CLAUDE_CODE_SHELL_PREFIX -u OBS_AUTH_TOKEN -u OBS_DISABLE -u OBS_SERVER_URL uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
   Note any failure that also fails at `f2bf0bd`, and leave it alone.
4. `grep -n 'OBS_DISABLE.*"1"\|OBS_DISABLE=1' .claude/skills/sssf/templates/adws/adw_modules/agent_pi.py .claude/skills/sssf/references/config.md`
   finds nothing (exit 1).
5. `git diff --stat main -- adws/ .claude/agents CLAUDE.md` shows no change
   to protected paths from this build.

## For the engineer

Nothing in this plan touches a protected path. The repo's own installed copy,
`adws/adw_modules/agent_pi.py`, keeps the old behaviour until the skill is
reinstalled into this repo. That's your call, after the merge.
