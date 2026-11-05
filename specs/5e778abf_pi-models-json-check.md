# Stop a container run when pi's models.json gives openai a credential

## Problem

Decision 10 of `docs/designs/container-operator.md`: in the operator container
(`SSSF_PI_TOKEN_SOCKET` set), a credential pi would use ahead of the broker's
token stops the run. `agent_pi.check_stored_login` reads only `auth.json`. pi
also reads `models.json` from the same agent dir. For a provider with no stored
login, pi 1.4.0 resolves a `models.json` `apiKey` ahead of the ambient
`OPENAI_API_KEY` (the broker's token). So an `openai` key there wins, and pi
bills that key instead of the host's ChatGPT plan without saying anything. An
agent in the container can write the shared home volume, so it can plant one.

## Baseline

`main` at `1052347` ("Say in install.md that run.sh needs XDG_RUNTIME_DIR").
The branch `guard-pi-models-json` is at the same commit. Judge every finding
against `1052347`.

## What pi 1.4.0 reads (findings from its source)

Installed pi: `@earendil-works/pi-coding-agent` 1.4.0 (`pi --version` prints
`1.4.0`). The files below are under its `dist/` and its bundled
`node_modules/@earendil-works/pi-ai/dist/`.

- **Path.** `config.js` `getModelsPath()` is `join(getAgentDir(), "models.json")`,
  and `core/model-runtime.js` `ModelRuntime.create` loads that path. pi itself
  reads no `PI_MODELS_PATH`. It is the same agent dir as `auth.json`.
- **Parsing.** `core/model-config.js` `ModelConfig.load`: `readFile` (follows a
  symlink). ENOENT means no config. Then `JSON.parse(stripJsonComments(stripBom(content)))`.
  `utils/json.js` `stripJsonComments` removes `//` line comments and trailing
  commas outside string literals. An invalid file or a schema failure makes pi
  ignore the whole file.
- **Schema.** `providers.<id>` takes `name, baseUrl, apiKey, api, oauth, headers,
  compat, authHeader, models[], modelOverrides{}`. Each `models[]` entry and
  each `modelOverrides` value may also have `headers`.
- **Fields that carry a credential:**
  1. `providers.openai.apiKey`. `ProviderConfigSchema` says *"Literal key,
     "$NAME" environment interpolation, or "!command" output"*
     (`core/resolve-config-value.js` does the resolving). In
     `core/provider-composer.js` `composeApiKeyAuth.resolve`, a configured
     `rawKey` is used whenever there is no stored credential. pi-ai's
     `auth/resolve.js` falls back to ambient env only through that same
     `resolve`, so the configured key wins over `OPENAI_API_KEY`. One field
     covers all three forms: literal, `$NAME`/`${NAME}`, and `!command`.
  2. `providers.openai.headers`, `providers.openai.models[i].headers`, and
     `providers.openai.modelOverrides.<id>.headers`. `HeadersSchema` says
     *"Values resolve like apiKey"*, so they can also be literal, `$NAME`, or
     `!command`. `withConfiguredAuth` merges provider headers over the auth
     headers, and `rawModelHeaders`/`resolveConfiguredModelHeaders` add the
     model ones to each request. A header such as `Authorization` or `api-key`
     sends a credential of its own. SSSF reads key names only and can't tell a
     credential header from any other, so any non-empty `headers` under
     `openai` stops the run.
- **Fields that carry no credential**, which pass: `name`, `api`, `baseUrl`,
  `compat`, `authHeader` (it only re-sends the key pi already resolved, as a
  Bearer header), `oauth` (`"radius"` only; a login it makes is stored in
  `auth.json` under `openai`, which the check already stops on), and
  `models[]`/`modelOverrides` model fields other than `headers`.
  `samplingParams` puts body fields into requests, and OpenAI takes no key in
  the body.
- **Other files pi reads from the agent dir** (`config.js`, `core/*`):
  `auth.json` (already checked); `settings.json`, which can list packages and
  extensions to load; `extensions/`, which pi loads automatically
  (`core/extensions/loader.js`); `models-store.json`, a model-list cache with no
  key fields; `mcp.json` and `mcp-auth.json`, for MCP only, not openai; and
  `themes/`, `tools/`, `bin/`, `prompts/`, `sessions/`, `keybindings.json`. None
  of them holds an openai credential as config. An extension, or a package that
  settings.json loads, is agent code, and it can register an `openai` key in
  code. That stays a known limit (see Out of scope).

## Threat model

- Agents run as the engineer's own user. In the container that is also the
  operator's user, so an agent can write anything in the home volume:
  `auth.json`, `models.json`, `settings.json`, `extensions/`.
- This guard runs in the operator process before every pi call on a local tree
  (`_in_container`, which also covers `pi --list-models` in `_pi_catalog`) and
  in the trial model check (`trial.pi_problems`). It reads two files by key name
  and stops on a credential. It runs no agent code.
- What it can't stop at its level:
  - A file swapped between the check and pi's read (the same TOCTOU as for
    `auth.json` today).
  - Agent code in pi's extensions dir or in settings.json packages, which can
    register any provider key in code.
  - A `baseUrl` that sends openai calls to a proxy that adds a key of its own.
  - Any process minting broker tokens (decision 9).
- Never read or print a value: no key, header value, or model id from the file
  goes into a message. Messages carry only the file path and the field path,
  built from key names and list indexes, such as
  `providers.openai.models[2].headers`. For `modelOverrides`, use the field
  path `providers.openai.modelOverrides.<key>.headers` with the literal word
  `<key>`, not the key itself.

## Bar

Against `1052347`, the change must:

- In the container (socket set, local tree), stop the run before its first pi
  agent and before `pi --list-models` when the agent dir's `models.json`:
  - has an `apiKey` key under `providers.openai`, whatever its value
    (`$OPENAI_API_KEY` included, since SSSF never reads values); or
  - has a `headers` value that is not an empty object under
    `providers.openai`, any `providers.openai.models[i]`, or any
    `providers.openai.modelOverrides.<id>`.
- Name the file and the field path in that stop, say to remove that entry, and
  never include a value.
- Read `models.json` under the `auth.json` rules:
  - stat first (following a symlink); a missing file passes;
  - a non-regular file stops the run without being opened;
  - `_read_regular` opens it with O_NONBLOCK and fstat-checks it;
  - anything it can't read or parse stops the run with a "can't read" message.
- Parse the file as pi does: strip a leading BOM, then `//` comments and
  trailing commas outside strings, then parse the JSON.
- Pass a `models.json` that adds models, overrides, `baseUrl`, `compat`,
  `authHeader`, empty `headers`, or keys for providers other than `openai`.
- Keep `auth.json` handling and its messages byte-for-byte as today, and check
  `auth.json` first.

It must never:

- change a host run (no socket), a VM run (remote tree), or a Claude Code
  roster in the container: no new reads, no new stops;
- run agent code, keep an agent file, or delete or overwrite any file (the
  check only reads);
- print or log a value from `models.json`.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Add `import re`.
- Leave `_pi_auth_json()` and `PI_AUTH_JSON` as they are. Derive the models
  path from the auth path's directory: `path.parent / "models.json"`. Both
  files sit in pi's agent dir (`getAgentDir`), and tests that monkeypatch
  `PI_AUTH_JSON` to `tmp_path / "auth.json"` then never read the developer's
  real `~/.pi/agent/models.json`. Don't use the existing `MODELS_JSON` /
  `PI_MODELS_PATH` constant, which pi doesn't read (see Out of scope).
- Add `_strip_json_comments(text: str) -> str`, a port of pi's
  `utils/json.js`, with a docstring naming that file:
  - first `re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*', lambda m: m.group(0) if m.group(0)[0] == '"' else "", text)`;
  - then `re.sub(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', lambda m: m.group(1) if m.group(1) is not None else m.group(0), text)`.
- Factor the stat/read part of `check_stored_login` into
  `_load_agent_json(path: Path, what: str, *, comments: bool = False) -> dict | None`.
  - Returns `None` when the file is missing (FileNotFoundError /
    NotADirectoryError on `os.stat`).
  - Keeps today's logic otherwise: stat, then a non-regular file stops; then
    `_read_regular`, `.decode("utf-8")`, and `json.loads` stop with "can't read"
    on any error or on a non-dict result.
  - When `comments` is set, removes one leading `"\ufeff"` and runs
    `_strip_json_comments` before `json.loads`.
  - Builds both messages from `what`, so `auth.json` keeps its exact text:
    - `f"{path} is not a regular file, so SSSF can't check it for {what}; remove it and start again."`
    - `f"SSSF can't read {path} to check it for {what}; fix or remove it."`
  - For `auth.json`, `what` is `"a stored ChatGPT login"`. For `models.json`,
    it is `"an openai key of its own"`.
- Add `_models_json_credential(data: dict) -> str | None`. It returns the first
  field path that carries a credential, or `None`. It reads key names only:
  - `providers = data.get("providers", {})`. If that is not a dict, raise the
    "can't read" stop. The caller passes `path` in, or raises when it sees a
    sentinel; pick one and keep it simple.
  - `openai = providers.get("openai")`. `None` means `None`. A non-dict stops
    with "can't read".
  - `"apiKey" in openai` gives `"providers.openai.apiKey"`.
  - `_has_headers(openai)` gives `"providers.openai.headers"`.
    `_has_headers(entry)` is `"headers" in entry and entry["headers"] != {}`.
  - `models = openai.get("models", [])`. It must be a list of dicts, or stop
    with "can't read". The first entry `i` with headers gives
    `f"providers.openai.models[{i}].headers"`.
  - `overrides = openai.get("modelOverrides", {})`. It must be a dict of
    dicts, or stop with "can't read". The first entry with headers gives
    `"providers.openai.modelOverrides.<key>.headers"`, with the literal text
    `<key>`.
- `check_stored_login(path=None)`:
  - Keep the relative-path stop as it is.
  - Then `data = _load_agent_json(path, "a stored ChatGPT login")`, and keep
    the `"openai" in data` stop and its message exactly. `None` means no
    auth.json, so carry on.
  - Then `models_path = path.parent / "models.json"` and
    `models = _load_agent_json(models_path, "an openai key of its own", comments=True)`.
    If that is not `None` and `_models_json_credential(models)` returns a
    field, raise:
    `f"{models_path} gives pi's openai provider a credential of its own ({field}). pi would use it instead of the host's sign-in, so remove that entry and start again; the container gets its token from the host."`
  - Update the docstring: it checks both files in pi's agent dir, under the
    same never-block and key-names-only rules, and names the credential fields
    pi 1.4.0 accepts in models.json (`apiKey` as a literal, `$NAME`, or
    `!command`, and `headers` at the provider, model, and override levels).
- Don't change `_in_container`, `trial.py`, or any caller. They already call
  `check_stored_login()` only in the container.

### 2. Tests: `.claude/skills/sssf/tests/test_agent_pi.py`

Add these tests. Use the existing `broker`, `catalog`, and `_shipped` helpers.
The shipped-config tests load `sssf.config.yaml` /
`sssf.config.claude_code.yaml` from `.claude/skills/sssf/templates/`. In every
stop test, assert that `"SECRET"` is not in the message.

1. `test_a_models_json_credential_stops` is parametrized over these openai
   provider bodies and expected field paths:
   - `{"apiKey": "SECRET"}` gives `providers.openai.apiKey`
   - `{"apiKey": "$SECRET_VAR"}` gives `providers.openai.apiKey`
   - `{"apiKey": "!echo SECRET"}` gives `providers.openai.apiKey`
   - `{"headers": {"Authorization": "Bearer SECRET"}}` gives `providers.openai.headers`
   - `{"models": [{"id": "m"}, {"id": "n", "headers": {"api-key": "SECRET"}}]}` gives `providers.openai.models[1].headers`
   - `{"modelOverrides": {"gpt-SECRET": {"headers": {"x": "SECRET"}}}}` gives `providers.openai.modelOverrides.<key>.headers`

   Write `{"providers": {"openai": body}}` to `tmp_path/"models.json"` and call
   `check_stored_login(tmp_path/"auth.json")` with no auth.json present. Assert
   that the message contains the models.json path and the field path.
2. `test_a_models_json_without_an_openai_credential_passes`: no file at all;
   then `{"providers": {"openai": {"baseUrl": "https://x", "authHeader": true, "headers": {}, "compat": {}, "models": [{"id": "m", "contextWindow": 1000, "reasoning": true}], "modelOverrides": {"gpt-5": {"maxTokens": 10}}}, "anthropic": {"apiKey": "k", "headers": {"a": "b"}}}}`;
   then `{}`. Each passes.
3. `test_models_json_is_parsed_like_pi`:
   - `"\ufeff{ // note\n \"providers\": {\"openai\": {\"apiKey\": \"SECRET\",},},}"`
     stops with `providers.openai.apiKey`.
   - A file whose only `//` sits inside a string
     (`{"providers": {"openai": {"baseUrl": "https://x//y"}}}`) passes.
   - A commented file with no key passes.
4. `test_an_unreadable_models_json_stops`: `not json` stops, matching
   `"can't read"` and the path. `{"providers": []}` stops the same way, and so
   do `{"providers": {"openai": "x"}}` and
   `{"providers": {"openai": {"models": "x"}}}`.
5. Extend `test_a_non_regular_auth_json_stops_without_blocking` with a second
   parameter, `name` in `["auth.json", "models.json"]`. Build the fixture at
   `tmp / name` and call `check_stored_login(tmp / "auth.json")`. Keep the
   5-second block assertion and the assertion that the path is in the message.
6. Extend `test_a_symlinked_auth_json_is_read_like_pi_reads_it`, or add a
   twin for `models.json`: a dangling link passes, and a link to a file with an
   openai `apiKey` stops.
7. Add `"models_key"` to `test_run_stops_before_pi_starts`. Write
   `(tmp_path / "models.json")` with an openai `apiKey`, and assert that
   `started == []`.
8. `test_a_models_json_key_stops_the_model_check` copies
   `test_a_stored_login_stops_the_model_check`: write models.json with an
   openai `apiKey`, then run `agents.validate` on the shipped
   `sssf.config.yaml`. It raises `SystemExit`, the message has
   `providers.openai.apiKey` and no `SECRET`, and `(tmp_path/"ran")` doesn't
   exist.
9. `test_a_host_run_ignores_models_json`: delete `SSSF_PI_TOKEN_SOCKET`, point
   `PI_AUTH_JSON` at `tmp_path/"auth.json"`, and write a models.json with an
   openai `apiKey`. `_in_container({"A": "1"}, sandbox.LOCAL)` returns
   `{"A": "1"}`. With the `broker` fixture and the same file,
   `_in_container({"A": "1"}, object())` (a VM tree) also returns `{"A": "1"}`.
10. `test_a_claude_code_roster_ignores_models_json`: as in
    `test_a_claude_code_roster_never_asks_the_broker`, plus a models.json with
    an openai `apiKey`. `agents.validate` on `sssf.config.claude_code.yaml`
    passes, and pi never runs.
11. Keep `test_a_stored_login_check`'s exact `"can't read"` match. Add one
    assertion that a stored `auth.json` openai login stops with the
    `openai entry` message even when models.json also has a key, so auth.json
    is checked first.

### 3. Docs

- `.claude/skills/sssf/references/config.md`:
  - Line 434 paragraph: after the `auth.json` sentence, add that the check also
    reads `models.json` in that same dir. A `providers.openai` entry there with
    an `apiKey` (a literal, `$NAME` or `!command`) or any `headers` (at the
    provider, a model, or a model override) stops the run, because pi would
    use it ahead of the broker's token. A non-regular or unreadable
    `models.json` stops the run too. Models and settings without a key pass.
  - Line 438 known limit: replace it with what stays true: "the check reads
    `auth.json` for an `openai` entry and `models.json` for an openai `apiKey`
    or `headers`. Other stored logins pi keeps are not checked." Add new known
    limits:
    - (a) code in pi's `extensions/` dir in the agent dir, or a package that
      `settings.json` loads, can register an openai key in code and isn't
      checked;
    - (b) a `baseUrl` for openai in `models.json` passes, so pi sends the
      broker's token and calls to that endpoint, which can add a key of its
      own;
    - (c) since SSSF reads key names only, `"apiKey": "$OPENAI_API_KEY"`
      stops the run too.
  - Lines 440 and 446: say "`auth.json` and `models.json`" where they say
    `auth.json`. That covers the TOCTOU limit, and the regular file with no
    end or no size limit.
- `docs/designs/container-operator.md` decision 10: add one sentence saying
  the same check reads `models.json` in that dir and stops on an openai
  `apiKey` or `headers`, because pi resolves a configured key ahead of
  `OPENAI_API_KEY`.
- Docstrings: `check_stored_login`, as in step 1.
- No CHANGELOG: the repo keeps none. No help text changes.
- `app_docs/`: don't edit it. For the documenter: any `app_docs/` write-up on
  the pi-in-container stored-login check (run 99c6f4fa or earlier) now
  describes the old behaviour, an `auth.json`-only check.

## Verification

From `.claude/skills/sssf/`:

    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_pi.py tests/test_trial.py tests/test_agent_cc.py tests/test_rosters.py

All must pass. Then the full suite:

    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests

To prove the tests catch the hole, stash only the change to
`.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` and run tests 1, 7
and 8. They must fail because nothing is raised. Restore the change. Report
the result, and don't commit.

Grep check: `grep -rn "SECRET" .claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`
finds nothing.

## Out of scope

These are old holes, to be filed as such:

- pi extensions in the agent dir's `extensions/`, and packages or extensions
  that `settings.json` lists, are code that can register an openai key or do
  anything else. A host run has the same exposure.
- A `baseUrl`, `api`, or `oauth: "radius"` change for openai in `models.json`.
  These hold no credential, but they redirect calls along with the broker's
  token.
- Keys for providers other than `openai`, and stored logins other than
  `openai` in `auth.json`.
- The TOCTOU between the check and pi's read, and a regular file whose read
  never ends (FUSE), now for both files.
- `agent_pi.MODELS_JSON` reads `PI_MODELS_PATH`, which pi 1.4.0 doesn't, and
  `context_window` parses models.json without stripping comments. Neither is a
  guard.
- `--sandbox vercel|e2b` launched from inside the container (existing known
  limit).

## For the engineer

These touch protected paths, so the builder must not do them:

- Once the build passes, stamp the change into the hub as `5aa3c66` did. Copy
  `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` into
  `adws/adw_modules/agent_pi.py`, and update `adws/adw_sssf_config/stamp.json`.
  Copy only this file.
- Optionally, re-run the real proof from the container in sssf-sandbox, as in
  decision 16. Put a `models.json` with `{"providers":{"openai":{"apiKey":"$NOPE"}}}`
  in the container's `~/.pi/agent/`. Validation should stop and name the file.
  Then remove it, and validation should pass.
