# Judge link targets and folder listing on fork build 05b3777f

Item 12 of the Next list in `docs/designs/agentsh-local-sandbox.md`. The fork build is described in that doc's section "The twelfth and thirteenth patches: link targets and listing, 2026-11-10". This change moves SSSF's rendered agentsh policies onto the build's two new judgments and pins the build. Nothing else changes.

## Baseline

`main` at `a47473d`, with build `472d63cf` pinned (`PINNED_SHA256 = f9da9802…9933e`). Every finding is judged against it. On `a47473d` with 472d63cf:

- The engine judges a link's own path and its target by the one operation `symlink`.
- `sssf-system-link` allows `symlink` on `/usr/bin/**` and `/bin/**`.
- `sssf-agent-config` (Claude only) and `sssf-git-config` deny `WRITE_OPS`. On 472d63cf those lists refuse a link to `~/.claude/settings.json`, `.git/config` and `.git/hooks/pre-push`.
- `HOME_READ` gives `READ`, which includes `list`, on `${HOME}` itself. In ptrace mode, 472d63cf never produces `list`, so an agent can list every folder it can open.

## Threat model

The agent runs as the engineer's own Unix user, inside `agentsh wrap` with ptrace interception. It can run any command the command rules allow, and it can try any syscall. The policy is the only layer that tells its access apart from the engineer's. The kernel only adds checks for files the user doesn't own, such as root-owned `/usr/bin`.

What the agent wants here:
- **Plant a link the engineer's tools follow.** Examples: a link at a path in `~/.local/share/uv/python/...` that leads to a script it wrote, which runs as the engineer the next time anything starts that name, or a link in the workspace that leads into `~/.claude/settings.json` or `.git/config`, which host git or an editor later writes through.
- **Learn names.** For example, list `~/github` or `~` to see the engineer's other repos and files.

What this guard can't stop at its level:
- A link is judged when it's made. If the link is moved later, or a folder on its path is swapped for a link, it can lead somewhere else. That matters only to tools outside the wrap. This is an old limit, and it stays in `config.md`.
- `stat` still says whether a name exists, and inotify and fanotify events carry the names that change in a watched folder. Neither is traced.
- The engine's own races: the "two swaps in flight" limit of the listing patch.
- Anything the agent can already write (workspace, temp, sessions, agent cache) it can also link into or from. The link adds nothing there.

## Bar

The earlier bars stand: everything the guard refused or allowed at `a47473d` it still refuses or allows, except for the changes this section names.

**Must work**, on build 05b3777f under the rendered policies:

1. `uv venv` on `/usr/bin/python3`.
2. `uv run --with` on a venv built on a uv-managed Python. sssf-sandbox's `.venv` is on managed 3.13.
3. `npm install`, `npm init -y`, `npx` (for example `npx --yes semver`), and `bun add`.
4. Claude Code exits 0 under the wrap.
5. A link to a file in the workspace.

**Must be refused**, on build 05b3777f under the rendered policies:

6. Links whose **target** is any of these, as 472d63cf refuses them today:
   - `~/.claude/settings.json` (Claude policy)
   - `<git dir>/config` or `<git dir>/hooks/pre-push`
   - the same two under `${PROJECT_ROOT}/.git`
7. A link to `/etc/shadow`.
8. A link whose **own path** is `<managed install>/bin/python3.99`, pointing at a file the agent wrote. More generally: a link whose own path is anywhere under `~/.local/share/uv`, `/usr/bin`, or any folder the baseline refused it in.
9. Every write, create, mkdir, rename, delete, link and chmod under `~/.local/share/uv`.
10. `ls ~/github`, `ls ~`, and Python's `os.listdir` of either.
11. Reading a sibling repo's files.

**Must still list:** `/`, `/home`, the workspace, the files and folders `HOME_READ` names, `~/.claude` (Claude), and `adws/adw_data` on a plain run, where it is inside the workspace. `open`, `stat`, `readlink` and `access` on `${HOME}` stay allowed.

05b3777f refuses `list` on every folder whose only allow rule leaves it out, not just `${HOME}` and `~/github`. That includes `sssf-mkdir-parents`' folders (`~/.cache`, `~/.local/share`, `/run/user/*`) and `sssf-config-folders`' folders. The hub's probes on the private server already ran under those rules: `npm install`, `bun add`, `uv run --with` and a Claude Code call worked. This change adds no `list` anywhere.

**Pin:** `PINNED_SHA256` is `603dbf1495da87c28be301f607efd00c66fb250633334204a0b04c2f10df1747`.

**Findings.** Each of these is a finding:

- **A new link target.** Any link target allowed beyond `/usr/bin/**`, `/bin/**` and `${HOME}/.local/share/uv/python/*/bin/python3.*` that the baseline refused. The rules that allow every operation still allow targets as before: workspace, git data, sessions, agent cache, temp, devices, fd leaves and agent write.
- **An agent action the baseline stops.** Keeping an agent file, running agent code, or deleting or overwriting an engineer file, where the baseline does otherwise.
- **A guard edit beyond the brief's six.** The six are:
  1. `symlink_target` added to `WRITE_OPS`.
  2. `sssf-system-link` moved to `symlink_target`, and the managed-Python glob added to `SYSTEM_LINK`.
  3. `${HOME}` opened but not listed: `""` leaves `HOME_READ`, and the new `sssf-home-folder` rule gives `FOLDER_OPS` on `${HOME}`.
  4. The pin and its comment.
  5. `config.md`.
  6. `docs/guide/install.md`.

  Two more edits are allowed, because they only follow from these six:
  - mirroring edits 1 to 3 in `docs/designs/agentsh/deny-policy.py`, which `test_file_rules_equal_the_designs_deny_policy_output` compares
  - the test changes in `.claude/skills/sssf/tests/test_agentsh_guard.py`

  Changing any other rule, op list, path, rule order, or any of `check_machine`, is a finding.
- **An overpromise in `config.md`.** Any `config.md` line that promises more than the probes in the design doc show. For example, a fresh `uv venv` on a managed Python: the probes show `uv run --with` on an existing venv, not a new venv.

## Out of scope

These are old holes, or holes this change leaves open on purpose. A reviewer files them as old holes:
- The fork's code. It was reviewed and tested outside this chain.
- The seccomp-notify file handler. SSSF doesn't use it, and it still judges a link's target as `symlink`.
- pnpm's two `npm_config_*` variables and npm's deprecation warning about them.
- The pin check's two old holes (Next item 1): an agent's edit to the project's `agentsh_guard.py` can repoint the pin, and a service left on an older binary passes.
- pi's policy (Next item 7).
- The listing patch's own limits: two swaps in flight, `stat` probing, inotify and fanotify, and the false `EINTR`/`EBUSY`.
- Links judged when made and moved later (an existing `config.md` limit).
- A link target into the agent cache's `uv-python`, which `sssf-agent-cache` already allows (see below).
- On a `--worktree` run, `adws/adw_data` and `adws` in the main checkout are opened but not listed. They are in `sssf-config-folders` with `FOLDER_OPS`, as on the baseline, where the engine didn't enforce it. Only the engine changed here.

## Does `UV_PYTHON_INSTALL_DIR` need the same allow? No.

`cache_env` sets `UV_PYTHON_INSTALL_DIR` to `<data_root>/agent-cache/uv-python`. `sssf-agent-cache` allows `ALL` (`*`) on `<data_root>/agent-cache/**`, and `*` covers `symlink_target`: the probes show a link to a workspace file, allowed only by `sssf-workspace`'s `*`, still works. So a venv linked to a Python in the agent cache already works, as `test_absolute_links_work_as_on_main` asserts today. A link *at* a path in that tree is allowed too, but the agent can already write any file there. `config.md` already says agents may edit that Python, so only wrapped agents' venvs may use it. Adding the glob there would change nothing. Do not touch it.

## What makes a run list the home folder

The code and the probes show these callers:
- **Claude Code** under the wrap lists `~/.claude` and the engineer's skill folders (`~/github/skills/**`), not `~`. The probe call exited 0.
- **uv** walks ancestors with `stat`, `access` and `readlink`, and opens config files by path. It never reads a folder's entries (the comment above `FOLDER_OPS`).
- **bun** opens each folder above the workspace. On `EACCES` from listing, it treats the folder as empty and goes on. `~` is in `sssf-config-folders` for a repo under home, so bun gets that `EACCES` there, as it already does for `~/github`.
- **Corepack** makes no folder call.
- **Node's module lookup** stats `~/node_modules` by name, which `stat` covers.

Nothing in SSSF's runs needs `list` on `~`. The hub's post-install probes check this (see "For the engineer").

## What changes

All code is in `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`.

1. **`WRITE_OPS`** (line 80): add `"symlink_target"` right after `"symlink"`:
   `WRITE_OPS = ["write", "create", "delete", "rename", "link", "symlink", "symlink_target", "mkdir", "rmdir", "chmod", "chown"]`
   This covers `sssf-agent-config` and `sssf-git-config`, its only two users.

2. **`SYSTEM_LINK`** (line 112):
   - Make it `["/usr/bin/**", "/bin/**", "${HOME}/.local/share/uv/python/*/bin/python3.*"]`.
   - In `file_rules`, change `rule("sssf-system-link", SYSTEM_LINK, ["symlink"], "allow")` to `["symlink_target"]`.
   - Rewrite the comment above `SYSTEM_LINK` to say that the rule allows a link's *target* only. The engine (05b3777f) judges a link's own path as `symlink` and its target as `symlink_target`, so the rule lets no agent make a link inside these trees. `/usr/bin` is root-owned anyway. Agents can't write `~/.local/share/uv` at all (no rule allows it), and a link at a path there is judged as `symlink`, which no rule allows. The glob covers the interpreter a venv on a uv-managed Python links to. The engine resolves the target through links first, so uv's minor-version folder links reach the patch folder's `bin/python3.x`. Keep the existing sentence about `/usr/lib`, `/usr/local`, `/opt`, `/mnt` and `/etc` being left out.

3. **The home folder is opened, not listed.**
   - Remove `""` from the `HOME_READ` tuple, so `${HOME}` itself is no longer in it. Leave every other entry as is.
   - Add a constant beside it: `HOME_FOLDER = ["${HOME}"]`. Give it a short comment: the home folder itself can be opened and checked but not listed, because its entries are the engineer's files and repos. Agents read the files and folders under it that `HOME_READ` names, and those stay listable.
   - In `file_rules`, add `rule("sssf-home-folder", HOME_FOLDER, FOLDER_OPS, "allow")` immediately **before** `sssf-home-read`. The tail becomes `… sssf-system-read, sssf-system-link, sssf-home-folder, sssf-home-read, sssf-default-deny`.
   - Update the `FOLDER_OPS` comment's last sentence. It now says the engine doesn't check reading entries, which is no longer true. Make it say that from build 05b3777f the engine judges reading a folder's entries (`getdents64`/`getdents`) as `list`, so these folders can be opened and checked but not listed.

4. **The pin** (line 40):
   `PINNED_SHA256 = "603dbf1495da87c28be301f607efd00c66fb250633334204a0b04c2f10df1747"   # build 05b3777f on sssf/links-listing`
   Nothing else in `check_machine` changes.

5. **The design's deny-policy mirror**, `docs/designs/agentsh/deny-policy.py`. `test_file_rules_equal_the_designs_deny_policy_output` asserts that its `file_rules` section equals `policy_text`'s, so make the same three edits there:
   - `WRITE_OPS` gets `symlink_target`.
   - `SYSTEM_LINK` gets the glob, and the `sssf-system-link` rule gets `["symlink_target"]`.
   - `HOME_READ` drops `""`, a new `HOME_FOLDER` is added, and the `sssf-home-folder` rule goes before `sssf-home-read`.
   - Update its comments the same way.
   - Do not regenerate `sssf-dd-*.yaml`: they are older outputs, and no test compares them.

6. **Tests**, `.claude/skills/sssf/tests/test_agentsh_guard.py`:
   - `Rules.link`: judge `link_path` with `"symlink"` and `target` with `"symlink_target"`, the 05b3777f engine. Update the docstring to say so. `first_match`'s fallback already returns `default-deny-files` for a non-READ op.
   - `test_absolute_links_work_as_on_main`:
     - The assertion that a link to `{HOME}/.local/share/uv/python/cpython/bin/python3.12` is denied becomes `== "allow"`.
     - Add: a link from `{R}/l` to `{G}/config`, `{G}/hooks/pre-push`, `{R}/.git/config` and `{R}/.git/hooks/pre-push` returns `"sssf-git-config"`.
     - When `world.claude`, a link to `{HOME}/.claude/settings.json` and `{HOME}/.claude/skills/x/SKILL.md` returns `"sssf-agent-config"`. For pi, assert a deny decision.
     - Keep the rest.
   - `test_links_to_system_pythons`:
     - The `SYSTEM_LINK` assertion becomes the three-entry list.
     - Assert that `rule_of(shipped_text(main, harness), "sssf-system-link")["operations"] == ["symlink_target"]`.
     - Rewrite the managed-interpreter comment and block. The link `{main}/.v/bin/python` → `{install}/bin/python3.13` is now `"allow"`.
     - These targets stay denied: `{install}/lib/python3.13/os.py`, `{install}/bin/pip`, `{HOME}/.local/share/uv/tools/ruff/bin/ruff`, and `{HOME}/.local/share/uv/python/x` (no `/bin/python3.`).
     - Keep the loop that denies a link at `{install}/bin/python3.99` and `.../sitecustomize.py` to every target. Add `{HOME}/.local/share/uv/python/x` and `{HOME}/.local/share/uv/x` to its paths.
     - Add a check that, for every path in `SYSTEM_LINK` with `${HOME}` substituted, `first_match(..., "symlink")` is not `"sssf-system-link"`.
   - New `test_links_judge_the_target_apart_from_the_path(harness)`, parametrized over both harnesses, on `shipped_text(MAINS[0], h)`:
     - `rule_of(text, "sssf-git-config")["operations"]` contains `"symlink_target"`, and so does `sssf-agent-config` for Claude.
     - For every allow rule whose operations aren't `["*"]`, `symlink_target` appears only in `sssf-system-link`. That is the bar item 2 guard.
   - New `test_the_home_folder_is_opened_not_listed(harness, main)`, over both harnesses and `MAINS + ["/srv/repo"]`. `/srv/repo` is outside home, so `sssf-config-folders` doesn't cover `${HOME}`. Use `Rules(shipped_text(main, h), HOME, main)` and assert:
     - `rule_of(text, "sssf-home-folder") == {"name": ..., "paths": ["${HOME}"], "operations": FOLDER_OPS, "decision": "allow"}`.
     - `"${HOME}"` is not in `sssf-home-read`'s paths.
     - `r.decision(r.first_match(HOME, "list")) == "deny"`.
     - Every op in `FOLDER_OPS` on `HOME` is allowed.
     - `list` on `/`, `/home`, `{HOME}/.config`, `{HOME}/.local/share/uv/python` and `{main}` is allowed.
     - `list` on `{HOME}/github` and on `{HOME}/github/sibling` is denied.
     - `read` of `{HOME}/github/sibling/README.md` is denied.
     - When Claude, `list` on `{HOME}/.claude` is allowed.
   - `test_the_file_rule_order_matches_main_around_the_new_allows`:
     - `order[-3:] == ["sssf-home-folder", "sssf-home-read", "sssf-default-deny"]`.
     - `order.index("sssf-home-folder") == order.index("sssf-system-link") + 1`.
     - Add `"sssf-home-folder"` to `wanted`.
   - `test_config_lookups_pass_and_the_rest_is_denied`: add `"symlink_target"` to the final `("write", "create", "delete", "symlink")` loop.
   - Do not test `PINNED_SHA256`'s value. The tests monkeypatch it.

7. **`.claude/skills/sssf/references/config.md`**, section "Running local agents under agentsh":
   - Second paragraph:
     - Replace "The folders themselves can be opened and checked, and that lets an agent list them; see the limits below." with: the folders themselves can be opened and checked but not listed.
     - Change "`uv venv` works on a system Python in `/usr/bin`." to: `uv venv` works on a system Python in `/usr/bin`, and an existing venv on one of your uv-managed Pythons works too.
     - Fix "read only the uv-managed Pythons and tools" so it doesn't promise that agents can't link to them. Suggested: "Agents no longer write `~/.cache/uv`, `~/.local/share/uv`, `~/.npm` or `~/.cargo`. They read only the uv-managed Pythons and tools, and may link to a managed interpreter."
     - Replace the build sentence: it needs agentsh `0.20.6-dev.fork+05b3777f`, the fork's commit `05b3777f` on `sssf/links-listing`, which is the build SSSF pins. Keep the existing list of what earlier builds get wrong, and add: let an agent list any folder it could open, and couldn't allow a venv on a uv-managed Python.
     - Add: install the build and reinstall with `just agentsh-install` together, since `check_machine` refuses while either differs. The new build refuses every `/usr/bin` link under the old policies, and the old policies would let an agent link to `~/.claude/settings.json` or `.git/config` under the new build.
   - Limits list:
     - **Drop** the "Under agentsh an agent can list every folder…" bullet.
     - **Drop** the sentence "A venv on a uv-managed Python can't be linked under the wrap: … judges them separately." from the "An absolute link may point into a system folder only…" bullet. Rewrite that bullet: an absolute link may point into a system folder only in `/usr/bin` (or `/bin`), or at a uv-managed interpreter (`~/.local/share/uv/python/*/bin/python3.*`), so a Python under `/usr/local` or `/opt` can't be a venv's base. agentsh judges a link's own path and its target apart, so no agent can make a link inside your uv tree.
     - **Fix** the "A fresh venv needs a Python in `/usr/bin`…" bullet. It says "uv can't use your managed Pythons or download one". The probes show `uv run --with` works on an existing venv built on managed 3.13, and `uv venv` works on `/usr/bin/python3`. They don't show `uv venv --python 3.13` making a fresh venv on a managed install. So change that sentence to: "Under the wrap, uv can't download a Python; an existing venv on one of your managed Pythons works." Leave the rest of the bullet, the system-Python and agent-cache install advice, as is.
     - **Add** a listing bullet: under agentsh an agent can list `/`, `/home`, its workspace, and on a plain run `adws/adw_data` (it's in the workspace), plus the folders it may read in your home, such as `~/.claude` for Claude Code. It can't list your home folder or `~/github`, so it can't list your other repos. `stat` still says whether a name exists, and `inotify` and `fanotify` events can show names that change in a watched folder.
   - Wording rule: every line names only what the probes in the design doc show. Don't claim `uv venv` on a managed Python unless it's in the probes. They show `uv run --with` on such a venv.

8. **`docs/guide/install.md`**:
   - Line 33: `0.20.6-dev.fork+472d63cf` becomes `0.20.6-dev.fork+05b3777f`.
   - Line 47: `sha256 \`f9da9802…9933e\`, commit \`472d63cf\`` becomes `sha256 \`603dbf14…f1747\`, commit \`05b3777f\``.

Files the builder touches, and no others:
- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`
- `.claude/skills/sssf/tests/test_agentsh_guard.py`
- `.claude/skills/sssf/references/config.md`
- `docs/designs/agentsh/deny-policy.py`
- `docs/guide/install.md`

Docs this makes out of date, which the builder does not edit:
- `app_docs/`: never edited by the builder. If a write-up there says the home folder or the folders above are listable, or that managed-Python links are refused, the documenter notes that the behaviour changed.
- `docs/designs/agentsh-local-sandbox.md` Next item 12, and `docs/open.md` line 33: these stay open until the engineer installs, so the hub session updates them after the probes (below).
- The repo keeps no CHANGELOG.

## Verification

The builder runs under agentsh on 472d63cf, and the skill suite reads the hub's `.env` at import. As in run 98e91ed7, copy `.claude` and `docs/designs/agentsh` to a fresh `/tmp` folder, keeping their relative paths so `DENY_POLICY` resolves, and run:

`uv run --no-config --directory <copy>/.claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py -q -p no:cacheprovider`

Then run the whole `tests` folder the same way. File and connect denials from the wrap cause known failures (run 6e904be8: 172 failed, 12 errors at its head). Compare your count with that, and report any new failure in `test_agentsh_guard.py`. SSSF's quality checks run outside the wrap, and they decide. The repo's test check is `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.

Must pass:
1. `test_agentsh_guard.py` in full: the new and changed tests, and `test_file_rules_equal_the_designs_deny_policy_output` for both harnesses.
2. **Render diff.** Diff the rendered policy against `a47473d` for both harnesses. Render it with `agentsh_guard.policy_text(repo_settings.RepoSettings(), "/m/proj/.git", h, "n", sessions="/m/proj/adws/adw_data/sessions", refuse_script="/m/proj/adws/adw_modules/sssf_refuse.py")` from this tree, and from `git show a47473d:.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` (with its sibling modules) in a temp folder. The diff must show only:
   - `symlink_target` added to `sssf-agent-config` (Claude) and `sssf-git-config`
   - `sssf-system-link`'s new path and its operations moved to `['symlink_target']`
   - the new `sssf-home-folder` rule
   - `${HOME}` gone from `sssf-home-read`

   Put the diff in the build notes.
3. `grep -n "PINNED_SHA256 =" .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows the new sha and `# build 05b3777f on sssf/links-listing`. `git diff a47473d -- <that file>` changes nothing in `check_machine`.
4. `config.md` check:
   - It names `05b3777f`, and nothing in it names `472d63cf` as the pinned build.
   - No line says an agent can list `~/github` or every folder above the workspace.
   - No line says a venv on a managed Python can't be linked.
   - The new listing bullet names `stat`, inotify and fanotify.
5. `grep -n "05b3777f\|603dbf14" docs/guide/install.md` shows both rows.

## For the engineer

Neither step is the builder's. Both happen after the merge:

1. **Update the hub's installed copy.** `adws/adw_modules/agentsh_guard.py` is protected, and the runner checks the pin its own checkout names. Update the hub to the merged skill the usual way (`install.py --update`, as in f0a3ae5), and do the same in sssf-sandbox.
2. **Install the build and the policies together.**
   - Install `~/.local/share/agentsh-builds/links-listing/out1/` as `/usr/bin/agentsh` (sha256 `603dbf14…f1747`).
   - Run `just agentsh-install` in both repos and the sudo lines it prints, with every agent stopped.
   - `check_machine` refuses while either the binary or the policies differ.

   The hub session then confirms that `check_machine` passes in both repos, and reruns in each:
   - `uv venv` on `/usr/bin/python3`, `uv run --with` on a venv built on a managed Python (sssf-sandbox), `npm install`, `npm init -y`, `npx --yes semver` and `bun add` all work.
   - A link to `/etc/shadow` is refused, and so is a link at `<managed install>/bin/python3.99` to a file the agent wrote.
   - Links to `~/.claude/settings.json`, `.git/config` and `.git/hooks/pre-push` are refused, and a link to a workspace file works.
   - `ls ~/github`, `ls ~` and `python3 -c 'import os; os.listdir(...)'` of each are refused. `ls /`, `ls /home` and `ls` of the workspace work, and a sibling repo's files stay unreadable.
   - A Claude Code call under the wrap exits 0, and its `agentsh_denials` trace shows no `list` denial it needed.
   - Then update `docs/designs/agentsh-local-sandbox.md` Next item 12 and `docs/open.md` line 33.
