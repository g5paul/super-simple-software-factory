# Put bun in the OpenShell image, and name a missing check tool before any agent starts

## Why

On 2026-11-18 a run on `sandbox: openshell` planned and built, then failed verify because the VM has no `bun` (`bun test` and `bun build` exited 127). It cost $0.25 and no source change could fix it. Two things change:

1. The OpenShell image gets bun, pinned.
2. Every remote provider's tool check also looks up, in the VM, the command names the repo's `quality.py` checks and `worktree_setup` run. A missing one stops the run in its `sandbox` phase, before any agent starts.

## Baseline

`main` at `a35abcb`. Every finding is judged against that commit.

At the baseline:

- `containerfile()` in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` installs node (pinned by `NODE`), uv (unpinned install script), Claude Code (unpinned `npm install -g`) and pi (pinned by `PI_VERSION`). No bun.
- `Remote._check_tools` in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py` runs one `sh -c` line of `<tool> --version` calls for `tools_needed(cfg)`, logs one `sandbox_tools` event, and raises `SandboxFailed` when `judge_tools` finds a problem. It reads nothing about the repo's own commands.
- SSSF reads `quality.py`'s commands in one place: `plan_checks.planned`, which calls the blocks with `quality._run` swapped for a recorder, after the plan and before the build. That runs the repo's Python. A custom block that skips `_run` runs its command for real.
- `worktree.set_up` reads `worktree_setup` with `repo_settings.load(worktree.main_checkout())`, which is `yaml.safe_load` and a pydantic model. No repo code runs.

## Decisions

### bun comes from npm, pinned

`RUN npm install -g bun@{BUN}` with `BUN = "1.4.4"`, the host's bun. `npm view bun@1.4.4` confirms the package exists, with `bin: {bun: bin/bun.exe, bunx: bin/bunx.exe}` and a `postinstall` of `node install.js`, which puts the platform binary in place.

Why npm and not bun's install script:

- The package spec names the version. npm checks each tarball against the integrity hash in the registry's metadata.
- It needs no new apt package (the script needs `unzip`) and no new build-time host. `registry.npmjs.org` already serves the Claude Code and pi lines.
- The install script is served live from `bun.sh`, so its text is unpinned even when its version argument is pinned.

It's no looser than its neighbours. The node line fetches a pinned tarball with no checksum. The uv line pipes an unpinned script to `sh`. The Claude Code line installs whatever is latest. The pi line is the same `npm install -g <name>@<pin>` form.

Where the binary lands: the build runs as root with node under `/usr/local`, so npm's global prefix is `/usr/local`. The binary is `/usr/local/lib/node_modules/bun/bin/bun.exe`, with the link `/usr/local/bin/bun`. `/usr/local/bin` is on the sandbox user's `PATH`, as it is for `node`, `claude` and `pi`. Root owns the files with mode 755, so `ubuntu` can run them and can't write them. The policy also lists `/usr` under `read_only`. The line must not pass `--ignore-scripts`, since the postinstall places the binary. A failed postinstall leaves a stub, and `bun --version` in the closing line then fails the build.

No build-time `runuser` or `su` proof is added: it can fail in a rootless Podman build for reasons that have nothing to do with bun. The on-image proof is under "For the engineer".

No policy change. `bun install` reaches `registry.npmjs.org` through the existing `package_registries` rule, which allows any binary.

### `quality.py` is read as text, never imported or called

The recorder in `plan_checks.planned` is how SSSF reads the commands today, but calling it at sandbox open would be a new run of the repo's code on the host: once more, earlier, and in chains that never call it. So the new check parses the file with `ast.parse` and runs nothing.

The file is `QUALITY_FILE = Path(__file__).with_name("quality.py")`: the `quality.py` beside the running `sandbox_remote.py`, which is the module the ADW imports. Tests point `QUALITY_FILE` at a probe file.

What counts as a check's command:

1. A **listed block** is a module-level `def` whose name appears as an `ast.Name` load inside the module-level functions `run_quality` or `run_tests`. A block neither names is skipped, since no chain runs it through them.
2. Inside a listed block, each `ast.Call` with an `argv=` keyword is one command.
3. `argv` is a list or tuple literal whose first element is a string constant: that string is the command name.
4. If the first three elements are the constants `sh` or `bash`, `-c`, and a string, the shell name is looked up and the string is read by the shell-string rules below. A third element that isn't a constant, such as an f-string, is skipped.
5. Anything else is skipped and logged: a call such as `_placeholder("test")` (why: `placeholder`), a name, a starred or computed first element, an `argv` that is one string.

A file that is missing, over 1 MiB, or that `ast.parse` can't read (`OSError`, `SyntaxError`, `ValueError`, `RecursionError`, `MemoryError`) gives no commands and one logged skip.

### `worktree_setup` is read the way the setup phase reads it

`repo_settings.load(worktree.main_checkout()).worktree_setup`, imported inside the function as `worktree.set_up` does. Any exception there gives no commands and one logged skip.

### Shell strings: read only the plain ones

Each `worktree_setup` entry, and each literal `sh -c` string from `quality.py`, is a shell string. The check doesn't parse shell. It reads only strings it can read without guessing:

1. If the string holds any character outside `[A-Za-z0-9_ \t./:@%+,=&|;-]`, the whole string is skipped. That covers quotes, `$`, backticks, parentheses, braces, redirects, backslash, `#`, `!`, globs, `~` and newlines.
2. Split on `&&`, `||`, `;` and `|`. A lone `&` left in a segment, or an empty segment, skips the whole string.
3. Each segment's first whitespace-separated word is a command name.
4. The whole string is skipped when it contains `PATH`, or when any segment's first word contains `=` or is one of `export`, `.`, `source`, `alias`, `eval`, `hash`, `set`, `unset`. Those can change what a later name means.

So `cd web && bun install --frozen-lockfile --backend=copyfile` gives `cd` and `bun`.

### Which names are looked up

Each name, from either source, goes through these in order. The first that matches decides.

| Case | Outcome |
|---|---|
| Holds a `/` (a script in the repo such as `./scripts/check.sh`, or any path) | skipped: `a path` |
| Doesn't fully match `[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}` (ASCII, `re.fullmatch`) | skipped: `not a plain command name`. Never sent to the VM |
| Already in this roster's `tools_needed` | nothing new: its version row covers it |
| `worktree_setup` may install it: the name appears in any non-first word of a parsed setup segment, or anywhere in a setup string that was skipped, with no `[A-Za-z0-9_]` character directly before or after it | skipped: `worktree_setup may install it` |
| Otherwise | looked up, once per name |

The fourth row is there because the lookup runs before setup. `npm install -g pnpm && pnpm install` must not stop on `pnpm`.

A placeholder block is skipped at the `argv` step. A literal `["echo", ...]` is looked up and found. A shell builtin such as `cd` is found, because `command -v` answers for builtins and keywords.

### The lookup: names are arguments

```python
LOOKUP = 'for n in "$@"; do if command -v "$n" >/dev/null 2>&1; then echo y; else echo n; fi; done'
out = self._must("sh", ["-c", LOOKUP, "sh", *names])
```

- The script is a constant. The names are positional arguments, never part of the script text.
- The name pattern already keeps out `;`, `$(...)`, newlines, spaces and a leading `-`, so a hostile first word is never sent at all. Both hold on their own.
- The VM answers `y` or `n` per name, in order, and never echoes a name. If the answer isn't exactly one `y` or `n` per name, every name is skipped (`the VM's answer couldn't be read`) and nothing stops.
- It goes through `_must` with no `env` and no `cwd`, like the version line, so it sees the `PATH` that `Remote.run` gives checks and setup (`_env` passes no `PATH`).
- With no name to look up, no call is made. A repo on the shipped placeholders with no `worktree_setup` sends the VM exactly `main`'s commands.

### What is logged and what stops

One `sandbox_tools` event, as on `main`, with:

- `tools`: the version rows as on `main`, plus one row per looked-up name, `"found"` or `"missing"`. `Remote.tools` holds the same map, so the `sandbox` phase log and the dashboard's VM hover show the rows with no visualizer change.
- `problems`: `main`'s, plus one per missing name, such as ``bun is missing; quality.py's `test` check runs it`` or `bun is missing; worktree_setup runs it`.
- `skipped`: a new key, always a list, at most 50 rows of `{"from": "quality:<block>" | "worktree_setup:<n>" | "quality.py" | "repo.yaml" | "vm", "what": <first 120 characters>, "why": <reason>}`.

`SandboxFailed` is raised when `problems` isn't empty, at the same point as on `main`. With roster problems only, the message is byte-for-byte `main`'s. With a missing command the message is:

`{provider} VM {vm} can't run this repo's checks or setup: {problems joined by "; "}. Nothing ran in it. Add the command to the VM's image or change the command; see docs/designs/remote-sandboxes.md, "As built: the tool check".`

`session.py` already calls `box.open()` inside the `sandbox` phase, and `Remote.open` already runs `_check_tools` last, so nothing changes there.

## Steps for the builder

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

- Add `BUN = "1.4.4"` on the line after `NODE`.
- In `containerfile()`, add `f"RUN npm install -g bun@{BUN}"` after the pi line.
- In the closing line, add `&& bun --version` right after `pi --version`. The line must still start with `RUN node --version`.
- Update the module docstring's image sentence to name bun.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

- Add `import ast`, and the constants `QUALITY_FILE`, `QUALITY_MAX = 1024 * 1024`, `NAME`, `LOOKUP`, `FOUND = "found"` and `SKIPPED_MAX = 50` near `TOOLS`.
- Add pure module-level functions, with no VM and no import of `quality`:
  - `quality_commands(path)` returns `(commands, skipped)` by the `ast` rules above.
  - `shell_words(text)` returns the first words of a plain shell string, or `None` when the string is skipped.
  - `repo_commands(quality_file, setup, known)` returns `(names, skipped)`, where `names` maps each name to look up to the source that named it first, in a stable order (`quality.py` first, then `worktree_setup`).
- Extend `_check_tools` as "Decisions" says:
  - keep the version call and `judge_tools` as they are;
  - call `repo_commands` inside `try/except Exception`, where a failure becomes one skip row;
  - make the lookup call only when there are names;
  - merge the rows into `tools`, add the problems, log the one event, raise.
- Update the comment above `TOOLS` and the `_check_tools` docstring.
- `judge_tools`, `tools_needed` and `TOOLS` keep their behaviour.

### 3. `.claude/skills/sssf/tests/test_sandbox_openshell.py`

Build each config with the file's `roster()` or `shipped()` helpers, which read the shipped `templates/sssf.config.yaml`. The fake VM is the host with `PATH=<tmp>/bin:/usr/bin:/bin`, so put stand-in scripts for the roster's tools (`uv`, and `claude` or `node` and `pi`) in the `cli` fixture's `bin` folder. Don't symlink. For a missing command use a made-up name such as `sssf-no-such-tool`, never `bun`, which the host may have.

Add a helper that opens a real `OpenShell` box through the fake CLI: `_create` patched to `osh._VM("sb")` as `_vm_box` does, `_check_tools` **not** patched, a small git repo, `sandbox_remote.TOKEN_FILE` pointed at a temp file, and `sandbox_remote.QUALITY_FILE` set by the test.

Tests:

- **The pin.** `containerfile(F, "")` holds the exact line `RUN npm install -g bun@1.4.4`, `osh.BUN == "1.4.4"`, and the closing line holds `bun --version`. Changing `BUN` changes `image_tag`.
- **The image check.** Add a `bun` stub to `test_the_image_check_fails_when_rg_is_missing`. Add a case where the `bun` stub is absent and the line exits non-zero.
- **A missing check command stops.** A probe `quality.py` whose listed block has `argv=["sssf-no-such-tool", "test"]`: `open()` raises `SandboxFailed` naming `sssf-no-such-tool`, the `sandbox_tools` event has it as `missing`, `box.processes == 0`, and no `setsid` exec is in the CLI log.
- **A missing setup command stops.** `adws/adw_sssf_config/repo.yaml` with `worktree_setup: ["cd web && sssf-no-such-tool install"]` stops the same way and names `worktree_setup`.
- **All present opens.** A check on `git` and a setup of `cd . && ls`: `open()` returns, the rows are `found`, and `problems == []`.
- **No false stop**, each opening with no problem and a skip row or a `found` row:
  - a literal `["echo", "PLACEHOLDER ..."]`;
  - `["cd", "x"]`, and a setup of `cd web`;
  - `["./scripts/check.sh"]` and `["scripts/sssf-no-such-tool"]`;
  - `argv=build_argv()`, `argv=[TOOL, "x"]`, `argv="sssf-no-such-tool x"`, and a `quality.py` that is a syntax error;
  - a block not named in `run_quality` or `run_tests`;
  - the setup strings `sssf-no-such-tool "a b"`, `export PATH=/x:/usr/bin; sssf-no-such-tool`, and `npm install -g sssf-no-such-tool && sssf-no-such-tool i` (with an `npm` stand-in);
  - a fake VM whose lookup answer is garbled.
- **The shipped template opens.** With `QUALITY_FILE` at its default, `templates/adws/adw_modules/quality.py`, and no `repo.yaml`: the exec calls in the CLI log hold no `LOOKUP` call, `tools` holds only the roster's rows, and `skipped` has five `placeholder` rows.
- **Nothing runs on the host.** A probe `quality.py` whose top level appends to a marker file: after `open()` the marker doesn't exist, and `"adw_modules.quality"` wasn't newly added to `sys.modules` by the call.
- **No injection.** First words `x; touch <marker>`, `$(touch <marker>)` and `a\ntouch <marker>`, in `argv` and in `worktree_setup`, beside one plain name. `open()` returns. Then:
  - every exec call that carries `LOOKUP` has the tail `["sh", "-c", sandbox_remote.LOOKUP, "sh", *names]` after the `"null"` word;
  - every name matches `NAME`;
  - no hostile string is in any logged argv;
  - the marker doesn't exist.
- **Events match.** With all commands present, the event names from `open()` equal `main`'s: `sandbox`, then `sandbox_tools`. The `sandbox_tools` payload has `main`'s keys plus `skipped`.

### 4. `.claude/skills/sssf/tests/test_sandbox_remote.py`

Using the `box` fixture (Vercel and E2B) and `sandbox_remote.QUALITY_FILE` set per test:

- A missing command makes `box._check_tools()` raise, naming it, on both providers.
- A present one is logged `found`.
- The fake's recorded calls for `_check_tools` are `main`'s version call plus one lookup call whose arguments are `["-c", LOOKUP, "sh", *names]`.
- With the default `QUALITY_FILE` there is no lookup call. `test_open_records_the_vms_tools_in_the_trace` must pass unchanged.
- Unit tests for `quality_commands`, `shell_words` and `repo_commands`, covering each row of the tables above.
- Mark `host_only` only what links files or needs the real host `PATH`.

### 5. Docs

- `.claude/skills/sssf/references/config.md`, "A local sandbox: `--sandbox openshell`":
  - list what the image holds: git, curl, python3, ripgrep, fd-find, node 24.11.1, uv, the repo's Python, Claude Code, pi 1.5.0 and bun 1.4.4;
  - say a repo can't add a tool yet;
  - say a check or `worktree_setup` command missing from the VM stops the run in its `sandbox` phase, on every provider, before any agent starts;
  - say what the check skips, and that a skipped command is in the `sandbox_tools` event.
- `docs/designs/openshell-sandbox.md`, "As built": extend "The image" bullet with bun, its pin, the npm source and why, where the binary lands, and the early stop.
- `docs/designs/remote-sandboxes.md`, "As built: the tool check": add the repo-command lookup, the skip rules, the `LOOKUP` form, and the new message. The error message points here.
- `docs/open.md`, under `## SSSF`, one row: a repo can't choose what its OpenShell image holds, so a repo that needs a tool beyond the fixed list stops at the tool check. The general fix is a repo-chosen tool list or Containerfile lines in `repo.yaml`, which is a new mechanism and a guard change. Feature, P3, Low, Ready, 2026-11-18. In its background, name what the tool check doesn't see (the list under "Out of scope").
- No `CHANGELOG` exists. `templates/adws/adw_modules/quality.py` is not edited: it is the file each repo customizes, and its comment about no pre-flight probe is still true of the local path.
- Never edit `app_docs/`. For the documenter: any write-up there on the VM tool check or the OpenShell image now describes old behaviour (no bun, roster tools only).

## Threat model

- The inputs are `quality.py` and `repo.yaml` (both under protected `adws/`) and the VM's answer. An agent runs as the engineer's own user, so an earlier run, or a builder whose `writes` names those files, can shape their text. This check must then do no more than skip or stop this run. It can't become a way to run something.
- On the host, the check runs no repo code. It parses `quality.py` as text and reads `repo.yaml` with `yaml.safe_load`. Hostile text can cause a skip or a false stop of its own run, nothing else.
- In the VM, the check runs one constant script. A name reaches it only as an argument, and only when it matches a plain-name pattern.
- The VM's answer is read as `y` or `n`. A hostile image can stop the run by saying `n`, which the provider could do anyway.
- What it can't stop: it is a cost guard, not a boundary. A pass doesn't prove the checks can run. After the check, an agent in the VM can remove or shadow a tool it may write (anything on Vercel or E2B with root, a user-writable `PATH` entry anywhere). Verify then fails with 127 as on `main`. An agent on the host can edit `quality.py` if its `writes` allows, and the next run reads that.

## Bar

Against `a35abcb`, the change must:

- hold a pinned bun install and `bun --version` in `containerfile()`, asserted by the pin;
- stop a run whose `quality.py` check or `worktree_setup` command is missing from the VM, in `open()`, naming the command, before any agent starts, on OpenShell, Vercel and E2B;
- log each found command in `sandbox_tools`, and each skip.

It must never:

- stop on a placeholder, a builtin, a path, a command it can't read, a name `worktree_setup` may install, or an unreadable VM answer;
- import or call `quality.py`, or run any repo code on the host that `main` doesn't;
- put a command name into shell text, or send the VM a name that isn't a plain command name;
- change `main`'s events, messages or VM commands for a run with nothing to look up, beyond the `skipped` key;
- keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't.

Every existing test passes.

## Verification

From `.claude/skills/sssf`, the touched files only, and one run at a time (the agentsh event store slows under load):

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_openshell.py tests/test_sandbox_remote.py tests/test_openshell_image.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

- Every new test in steps 3 and 4 passes, and every existing test in those files passes.
- `uv run .claude/skills/sssf/scripts/openshell_image.py --print` from the repo root shows the bun line and the new tag. Don't build the image.
- The whole suite is left to verify, through the repo's own `quality.py`. No test starts a real VM or runs Podman.

## Out of scope

Holes this change leaves open, all old or accepted:

- A repo-chosen tool list or Containerfile lines in `repo.yaml` (the new `docs/open.md` row).
- Only a command's first word is looked up. A tool behind a wrapper is not seen: `uv run pytest`, `npx`, `env X=1 bun`, `bun x tsc`. A computed `argv` or an f-string shell string is skipped, so the hub's own ``bash -c f"cd {VISUALIZER} && bun test"`` gets only `bash` looked up.
- A plan's own `checks` aren't known at sandbox open and aren't looked up.
- A block the chain reaches some other way than `run_quality` or `run_tests` is skipped.
- A tool's version isn't judged, so Vercel's or E2B's bun may differ from 1.4.4. E2B's template installs bun unpinned.
- The Claude Code and uv lines of the Containerfile stay unpinned.
- A tool removed or shadowed in the VM after the check.
- Building any repo's image, the cookbooks' switch steps and install step 12, the P2 row on committed secret files, and the other open rows.

## For the engineer

- After the merge, update each project's stamped copy (`install.py --update`), the hub's `adws/` included, then run `just openshell-setup` in each repo on OpenShell. Every repo's image tag changes.
- Prove the binary on a built image, in a sandbox as `ubuntu`:
  `command -v bun && bun --version && test ! -w "$(readlink -f "$(command -v bun)")"`
  It should print `/usr/local/bin/bun` and `1.4.4`, and exit 0.
- The hub session edits the cookbooks on `main` after this run.
