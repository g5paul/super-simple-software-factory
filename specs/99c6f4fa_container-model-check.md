# Plan: let the roster's model check sign in through the container's broker

## Problem

In the operator container (`SSSF_PI_TOKEN_SOCKET` set), pi has no stored login
(decision 10 of `docs/designs/container-operator.md` stops a run that has one).
`agent_pi._pi_catalog` runs `pi --list-models` with `operator_env()` alone, so
without `OPENAI_API_KEY` pi lists no `openai` models and `agents.validate` stops
every ChatGPT roster:

    agent 'planner': model pattern 'openai/gpt-6.1-sol' not found in pi --list-models

The broker is only asked per agent call (`agent_pi._in_container`), and that
happens after validation. The same gap exists in `trial.pi_problems`, which runs
`pi auth check --provider openai` in the container. That check can't pass there,
because the container's pi has no login. A trial of a ChatGPT roster from the
container would still stop after this fix unless it is covered too.

## Baseline

`main` at `feed3f8` ("Stamp pi in the container into the hub"). The branch
`guard-pi-container-models` is at the same commit. Judge every finding against
`feed3f8`.

## Threat model

- Agents run as the engineer's own user. In the container that is the same user
  as the operator, and (decision 9) any process there can already ask the socket
  for a one-hour ChatGPT access token. This change gives agents nothing new:
  the extra broker call happens in the operator process, before any agent
  starts, and its token goes only into the environment of one short-lived
  `pi --list-models` (or nothing, for the trial check) child.
- Agents cannot change the operator's environment, so they can't point
  `SSSF_PI_TOKEN_SOCKET` or `PI_PATH` somewhere else for the operator.
- Out of reach at this level: an agent in an earlier run that plants a `pi` on
  the container's `PATH`, or edits files in the home volume, gets the token the
  next validation hands that `pi`. Every pi agent call already hands the same
  token to the same binary, so this is an old hole, not a new one.
- The token must never be printed, logged, put in an exception message, kept in
  the `lru_cache` of `_pi_catalog`, or written into `os.environ` (where it would
  reach Claude Code agents and quality blocks).

## Bar

The change must:

1. When `SSSF_PI_TOKEN_SOCKET` is set, run `pi --list-models` with a broker
   token as `OPENAI_API_KEY`, through the same `_in_container` path an agent
   call uses: stored-login check first, then `container_token`.
2. Stop validation on a broker error (or a stored `openai` login, or a missing
   socket) with that `SandboxFailed` message, as a `SystemExit` that starts with
   `config validation failed:`. It stops at the first such error, without
   asking the broker again for each remaining pi agent, and before any agent
   or session starts.
3. Give `trial.pi_problems("openai")` the same treatment in the container: ask
   the broker instead of running `pi auth check`, and report its error as the
   problem.
4. Keep the token out of every message, log line, the catalog cache, and
   `os.environ`.

The change must never, against the baseline:

- change anything on a host run (no `SSSF_PI_TOKEN_SOCKET`), in a VM run from
  the host, or for a Claude Code roster in the container. A Claude Code roster
  never reaches `_pi_catalog`, so it never asks the broker.
- keep an agent's file, run agent code, or delete or overwrite an engineer's
  file.
- weaken any check on the host: `pi_problems` keeps its `pi auth check`,
  `oauth`, and `SSSF_PI_AUTH` checks unchanged whenever the socket variable is
  unset, and for every provider but `openai` in the container.

## Changes

All paths are from the repo root. Guard code lives under
`.claude/skills/sssf/templates/adws/adw_modules/`. Do not edit anything under
`adws/` (the hub's stamped copy is protected; see "For the engineer").

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

In `_pi_catalog`, build the child's environment with the container path before
running pi:

```python
@lru_cache(maxsize=1)
def _pi_catalog() -> list[tuple[str, str, int]]:
    """Read pi's merged catalog, including built-in providers and custom models.

    pi runs here, on this machine, whatever tree the agents use. In the operator
    container it has no login of its own, so it gets the host broker's token as
    OPENAI_API_KEY (_in_container), as an agent call does; without it pi lists
    no openai models. A broker error raises SandboxFailed and nothing is cached.
    The cache holds the parsed rows only, never the environment."""
    env = _in_container(operator_env(), sandbox.LOCAL)
    try:
        result = subprocess.run(
            [PI_PATH, "--list-models"], capture_output=True, text=True,
            timeout=30, env=env, check=False,
        )
    ...  # rest unchanged
```

- Always pass `sandbox.LOCAL`: the catalog is a local subprocess even on a VM
  run, and `_in_container` is a no-op when the socket variable is unset, so the
  host and VM-from-host paths are byte-for-byte today's.
- `_in_container` is defined later in the module. That is fine, since the name
  is looked up at call time. Don't move code around.
- `operator_env()` returns a copy, so `os.environ` is untouched.
- Do not catch `SandboxFailed` here. `lru_cache` doesn't cache an exception, so
  a later call asks again.
- Update the `_in_container` docstring to say it serves the catalog as well as
  agent calls.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

In `validate`, around `agent_pi.resolve_model(agent.model)`:

```python
        try:
            agent_pi.resolve_model(agent.model)
        except ValueError as e:
            problems.append(f"agent {name!r}: {e}")
        except sandbox.SandboxFailed as e:
            # The container's broker couldn't sign pi in: no pi agent can run,
            # so stop now rather than ask it again for every agent.
            raise SystemExit(f"config validation failed:\n- agent {name!r}: {e}") from None
```

Add `sandbox` to the existing `from . import (...)` list at the top if it isn't
already there. Make sure no import cycle appears: `agent_pi` already imports
`sandbox`, so importing it in `agents` is safe. `validate` runs before
`session.ensure`, so a raw `SandboxFailed` would be a traceback. `SystemExit`
matches the other validation failures, and `trial.problems` already turns a
`SystemExit` from `validate` into a roster problem.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/trial.py`

At the top of `pi_problems(made_by)`:

```python
    socket_path = os.environ.get(agent_pi.CONTAINER_SOCKET_VAR)
    if made_by == "openai" and socket_path:
        # In the operator container pi has no login: every openai call gets the
        # host broker's token (agent_pi._in_container), so ask the broker the
        # same way. It refuses an API key, and a failed broker stops the call
        # rather than billing a key, so the oauth and SSSF_PI_AUTH checks below
        # have nothing to add here.
        try:
            agent_pi.check_stored_login()
            agent_pi.container_token(socket_path)
        except sandbox.SandboxFailed as error:
            return [str(error)]
        return []
```

Leave the rest of `pi_problems` unchanged. Don't bind the returned token to a
name that gets logged or returned. Update the docstring of `pi_problems` to say
that in the container the broker answers for `openai`.

### 4. Tests: `.claude/skills/sssf/tests/test_agent_pi.py`

Add these under the existing `# --- the operator container ---` section. Reuse
the `broker` fixture, which sets `SSSF_PI_TOKEN_SOCKET` and points
`PI_AUTH_JSON` at `tmp_path / "auth.json"`. Add a small autouse-free helper
fixture, `catalog`, that calls `agent_pi._pi_catalog.cache_clear()` before and
after each test, so no real or earlier catalog leaks in or out.

Helper `_fake_catalog_pi(tmp_path, monkeypatch, cfg)`:
- Collects every `agent.model` of the pi agents in `cfg` (split into provider
  and id at the first `/`).
- Writes an executable `tmp_path / "pi"` shell script. When it gets
  `--list-models`, it prints a header line, then one row per non-`openai` model
  (`<provider> <id> 200K`), and the `openai` rows only when
  `[ -n "$OPENAI_API_KEY" ]`. It also writes `$OPENAI_API_KEY` to
  `tmp_path / "seen_key"` and touches `tmp_path / "ran"`.
- `monkeypatch.setattr(agent_pi, "PI_PATH", str(script))`.

Build every config from the shipped rosters with
`agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))` (and
`sssf.config.claude_code.yaml` for the Claude Code case). Never write a
test-only roster. `monkeypatch.chdir(tmp_path)` and create every agent's
`prompt_engineering.system`/`user` file (empty, parents made), so the only
problems `validate` can report are model ones. Validate with
`[a.name for a in cfg.agents]`. Import `agents` alongside the existing
`agent_pi, sandbox` import.

Tests:

1. `test_the_model_check_signs_in_through_the_broker(broker, catalog, ...)`:
   with `OPENAI_API_KEY` deleted from the environment, `agents.validate(cfg,
   names)` returns. `seen_key` holds `eyJ-tok`. `os.environ` has no
   `OPENAI_API_KEY` afterward. No cached row from `agent_pi._pi_catalog()`
   contains `eyJ-tok`. **This test must fail on `feed3f8`**, with the
   `not found in pi --list-models` problem for the `openai/...` agents.
2. `test_a_broker_error_stops_the_model_check_with_its_message`:
   `broker["answer"] = b"ERR sign in with pi, then /login\n"`. `validate`
   raises `SystemExit`. Its text starts with `config validation failed:`,
   contains `sign in with pi, then /login`, and does not contain
   `not found in pi --list-models`. The fake pi never ran (`ran` absent).
3. `test_a_stored_login_stops_the_model_check`: write
   `{"openai": {"access": "SECRET"}}` to `tmp_path / "auth.json"`. `validate`
   raises `SystemExit` mentioning the `openai` entry, without `SECRET`.
4. `test_an_api_key_from_the_broker_stops_the_model_check_without_echoing_it`:
   answer `b"OK sk-leak\n"`. The `SystemExit` text doesn't contain `sk-leak`.
5. `test_a_host_model_check_is_unchanged`: socket variable deleted, `OPENAI_API_KEY`
   unset. `validate` raises with `not found in pi --list-models` (today's
   behaviour). Then with `OPENAI_API_KEY=sk-op` set and the cache cleared, it
   passes, and `seen_key` holds `sk-op`, not a broker token.
6. `test_a_claude_code_roster_never_asks_the_broker`: broker answers `ERR`,
   config is `sssf.config.claude_code.yaml`. `validate` returns, and the fake
   pi never ran.

### 5. Tests: `.claude/skills/sssf/tests/test_trial.py`

Add, next to `test_pi_on_openai_needs_the_chatgpt_sign_in_and_subscription_auth`,
a test that starts its own fake broker. Copy the small `broker` fixture pattern
from `test_agent_pi.py` into `test_trial.py`. Don't import across test modules.
Point `agent_pi.PI_AUTH_JSON` at a tmp path.
- With `OK eyJ-tok`: `trial.pi_problems("openai") == []`, and
  `trial.subprocess.run` is never called (monkeypatch it to raise).
- With `ERR nope`: `trial.pi_problems("openai") == ["nope"]`.
- With the socket set, `trial.pi_problems("google")` still runs `pi auth check`
  (the monkeypatched `subprocess.run` answer is used, as in the existing test).

### 6. Docs

- `.claude/skills/sssf/references/config.md`, section "pi in the operator
  container": after the sentence about each pi agent call, add that config
  validation's model check (`pi --list-models`) and a trial's sign-in check for
  `openai` ask the broker the same way. A broker error, or a stored `openai`
  login, therefore stops validation with its message before any agent starts,
  instead of showing up as a missing model. A Claude Code roster never asks.
  Also add to line 108's bullet ("`pi --list-models` is the catalog the
  resolver actually reads") that in the container it reads it with the
  broker's token.
- `docs/designs/container-operator.md`:
  - Decision 11: "fails at its first call with the broker's error" becomes
    "fails at config validation, before any agent, with the broker's error".
  - Decision 8: after "For each pi agent call, `agent_pi` asks the socket…", add
    that the roster's model check (`pi --list-models`) and the trial's openai
    sign-in check ask it too.
  - `## Log`: add a row:
    `| 2026-11-05 | sssf-sandbox | first pi-roster proof from the container | failed at config validation: pi --list-models had no token, so no openai models; fixed by giving the model check the broker's token |`
- Docstrings: `_pi_catalog`, `_in_container`, `pi_problems` as above.
- No README change. Line 106 already says a pi roster needs the host signed in
  to pi. The repo keeps no CHANGELOG.
- `app_docs/`: don't edit. For the documenter: any `app_docs/` write-up of the
  pi-in-container change that says a host without a sign-in fails "at the first
  pi agent call" now describes old behaviour. It now fails at config validation.

## Verification

From `.claude/skills/sssf/`:

    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_pi.py tests/test_trial.py tests/test_agent_cc.py tests/test_rosters.py

All must pass. Then the full suite:

    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests

To prove the new test catches the bug, stash only the change to
`.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` (or check
`feed3f8`'s version out into a scratch copy) and run test 1. It must fail with
`not found in pi --list-models`. Restore the change afterward. Report the
result; don't commit.

Grep check: `grep -n "eyJ-tok\|sk-leak" -r .claude/skills/sssf/templates` finds
nothing (no test token leaked into shipped code).

## Out of scope

Old holes this change leaves open, to be filed as such:

- `vm_token` (`--sandbox vercel|e2b` launched from inside the container) still
  mints VM tokens with the container's own pi, not the broker. This is a known
  limit already in `config.md`.
- Any process in the container can mint one-hour tokens from the socket
  (decision 9), and each connection starts a host `pi` with no cap.
- A `pi` planted earlier on the container's `PATH`, or a swapped `auth.json`
  between check and use, gets the token at validation exactly as it already does
  at each agent call.
- The stored-login check still looks for an `openai` entry only.
- The broker token for the catalog is asked for even when a container roster
  names no `openai` model. This matches agent calls, which already ask on every
  pi call.
- `pi auth check` for providers other than `openai` in the container is
  unchanged.

## For the engineer

These touch protected paths, so the builder must not do them:

- Stamp the change into the hub after the build passes, as `feed3f8` did. Copy
  `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`, `agents.py`, and
  `trial.py` into `adws/adw_modules/`, and update
  `adws/adw_sssf_config/stamp.json`. Note that `adws/adw_modules/quality.py`
  already differs from its template, so copy only these three files, not the
  whole directory.
- Re-run the real proof in sssf-sandbox from the container (decision 16) with
  a ChatGPT roster. Validation should pass, and the planner should start.
