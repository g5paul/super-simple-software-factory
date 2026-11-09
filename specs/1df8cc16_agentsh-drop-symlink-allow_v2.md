# Finish the real-run fixes without the symlink allow

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md`. Source task: `specs/tasks/guard-agentsh-wrap-5.md`. This revises `specs/8f8304ae_agentsh-real-run-fixes_v2.md`. The chain run 8f8304ae stopped after round 2, and every finding came from item 6, the relative-symlink allow (`docs/designs/agentsh/guard-run-8f8304ae.md`). The engineer's decisions are in the design doc's section "The chain run for the real-run fixes, 8f8304ae". They're settled, so don't reopen them.

Revision 2. It answers spec review 1:
- The test of the round-2 false positive is dropped. It built a test-only `RepoSettings`, and its mechanism, `relative_secret_forms`, goes away with this change. Test 3 guards against item 6 coming back.
- The `config.md` symlink line keeps the sentence about targets that go through an existing symlink, and adds the absolute-`..` case.
- The alias line in `config.md` names the later-argument case.
- Test 1 asks the builder to check the shipped secret globs against its bare targets.

The branch already holds the reviewed code for items 1 to 5 and 7, through snapshot cee7192. **Keep it.** This plan makes three changes only:
- it removes item 6,
- it widens the alias pattern,
- it records two known limits.

## Threat model

Each local Claude Code or pi agent runs as the engineer's own user under `agentsh wrap`, with a default-deny policy that SSSF generates. The agent can:
- run any command,
- write any file in its workspace,
- make links,
- leave files that SSSF and host git later use outside the wrap. Examples are `plan.md` in the handoff folder, and git run with `cwd=run.repo_root` in `permissions.py`.

The guard sees what the ptrace tracer reports:
- execve argv, joined by spaces with `argv[0]` first,
- file operations on resolved paths,
- connects.

For a `symlinkat`, the engine checks two things with operation `symlink`, and both must allow:
- the link's own path, resolved,
- the target, as the raw string the agent passed.

No absolute rule can judge a relative target, so SSSF stops trying. A relative target matches no SSSF rule and falls to agentsh's own `default-deny-files`.

At this level the guard can't stop:
- a process that leaves the wrap,
- a copied binary under a new name,
- what host-side tools do with files or links the agent left behind,
- quality checks, which run outside the wrap.

The text guard (`rules.py`, `sssf_refuse.py`) stays on and is unchanged.

## Baseline

`main` at `b307ef3`. Every finding is judged against that commit. On `main`:
- the file rules have no `sssf-link-targets`, `sssf-relative-links`, `sssf-sessions` or `sssf-refuse-script`,
- `sssf-no-push` is `['^\S*\s+push(\s|$)', '(?s)^\S*\s+-.*push']`.

## Bar

Must do, against the baseline:
1. **The push rule.**
   - `sssf-no-push`'s patterns equal the three in `docs/designs/agentsh/sssf-net.yaml`. The third is `(?is)^\S*\s+-.*\salias\.[^=\s]*=.*push`.
   - The five real argv lists in the fixture run.
   - These run:
     - `git -C /x/push-tools status`,
     - `git -c hook.pre-push.enabled=false status`,
     - `git -c alias.st=status st`,
     - `git -c alias.lg='log --oneline' lg`.
   - Every case in `test_push_cases_that_are_denied` stays denied. These are denied too, in any case of key or value:
     - `git -c ALIAS.p=push p`,
     - `git -c alias.p=PUSH p`,
     - `git -c 'alias.p=!f(){ git push; }; f' p`,
     - `git -c 'alias.p=!git;push' p`,
     - `git -c 'alias.p=!git "push"' p`,
     - `git -c "alias.p=!sh -c 'git push'" p`.
2. Items 2 to 5 and 7 of the last spec are unchanged:
   - the refuse-script read (claude only),
   - the `null` page,
   - the command named from argv,
   - the sessions folder allow, after every deny,
   - the real-data fixtures and tests.
3. **A relative symlink is denied, as on `main`.** A link made in the workspace, the sessions folder, temp or a git dir, with a target that doesn't start with `/`, gets a deny. In the workspace, the sessions folder and temp, that deny is agentsh's `default-deny-files`, which the emulation shows as `default-deny-files`.
4. **An absolute link in an allowed place works as on `main`.** For example:
   - `.venv/bin/python -> ${HOME}/.local/share/uv/python/.../python3.12` (`uv venv`),
   - `<root>/l -> <root>/src/a`.

Must never do, against the baseline:
- keep an agent file,
- run agent code,
- delete or overwrite an engineer file,
- allow a path the baseline denies, beyond items 2 and 5 of the last spec (refuse-script read, sessions folder),
- let a run stop because the API answered normally.

An item 6 mechanism coming back in any form is a finding:
- an allow on relative link targets, meaning any allow rule path that doesn't start with `/` or `${`, other than `main`'s `sssf-env-examples`,
- a list of link folders,
- a special case for secret names in link targets.

Accepted, not findings:
- a relative symlink denied by the engine's fallback. That includes an agent's `ln -s`, `bun add` and `npm install`, since `node_modules/.bin` links are relative,
- the workspace-root swap limit below,
- the item-5 allow on every run's handoff folder,
- every limit `config.md` already lists.

A real Claude Code call that the policy denies, with no agent action behind it, is a false positive.

## Out of scope

These stay open. File them as old holes:
- **The workspace root swapped for a link.** `mv <root> elsewhere && ln -s /tmp/evil <root>` passes, as on `main`. Host git in `run.repo_root` would follow the link. The after-call guard's swapped-directory check is expected to stop the run, though no one has run it under the service.
- **An absolute link target holding `..`.** For example, `<root>/l -> <root>/../../home/u/.ssh/id_rsa` is matched as raw text against `sssf-workspace` and allowed, as on `main`. Item 6's `**..**` deny closed this, and it goes away with item 6.
- **A link target that passes through an existing symlink.** The engine judges it by its text, as on `main`.
- Moving the handoff folder into the worktree.
- A fork patch so agentsh resolves a link's target against its folder.
- pi under the wrap.
- Running a real agent under the service.
- sudo.
- Turning this on outside sssf-sandbox.
- **Glob metacharacters in the main checkout's path.** `rule()` doesn't escape them.
- Agents reading other runs' handoff files. The engineer accepted this.
- The push forms `config.md` still lists. They reach exec without `push` in an alias value, and the network rules refuse GitHub:
  - `GIT_CONFIG_COUNT`,
  - `include.path`,
  - `--config-env`,
  - `GIT_CONFIG_GLOBAL`,
  - `git-push` by path,
  - `send-pack`,
  - an `argv[0]` holding whitespace.

## Files and changes

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

Read it whole first. Then:

1. **Remove item 6.**
   - Delete `LINK_FOLDERS` and the two comment lines above it.
   - Delete the function `relative_secret_forms`.
   - In `file_rules`, delete the `sssf-link-targets` rule and its three comment lines. `sssf-workspace` then follows `sssf-git-config` directly, as on `main`.
   - Delete the `sssf-relative-links` rule and its two comment lines. `sssf-home-read` is then second to last and `sssf-default-deny` last, as on `main`.
   - Leave everything else in `file_rules` as it is, including the keyword-only `sessions` and `refuse_script` arguments:
     - `sssf-sessions` stays right after `sssf-git-data`,
     - the claude-only `sssf-refuse-script` stays after it.
2. **Widen the alias pattern.** In `COMMAND_HEAD`, the `args_patterns` line becomes exactly this. Keep the Python string escapes: the file writes `\\S` inside a normal string.

   `    args_patterns: ['^\\S*\\s+push(\\s|$)', '(?s)^\\S*\\s+-.*\\spush(\\s|$)', '(?is)^\\S*\\s+-.*\\salias\\.[^=\\s]*=.*push']`

   Once parsed from YAML, the list must equal `docs/designs/agentsh/sssf-net.yaml`'s `sssf-no-push` list.

   Rewrite the comment above it to say the following, and drop the lines about values that pass:
   - `push` must be a word of its own after a leading option, or appear anywhere in an alias's value or after it.
   - A hook name such as `hook.pre-push.enabled`, or a path such as `/x/push-tools`, doesn't match.
   - The alias pattern ignores case, because git reads config keys in any case. It catches `ALIAS.p=push` and `alias.p=PUSH`.
   - `(?s)` and `(?is)` are there so a newline in an option's value can't hide the verb.

### `docs/designs/agentsh/deny-policy.py`

`test_file_rules_equal_the_designs_deny_policy_output` compares this script's file rules with the module's. Make the same removals here:
- `LINK_FOLDERS`,
- the `sssf-link-targets` rule and its comments,
- the `sssf-relative-links` rule and its comment.

Keep the sessions and refuse-script rules, and the `main()` derivation of `sessions` and `refuse`. Its command rules come from `sssf-net.yaml`, which already holds the new pattern, so nothing else changes.

Don't touch `docs/designs/agentsh/sssf-dd-claude.yaml`, `sssf-dd-pi.yaml` or `sssf-net.yaml`. The `sssf-dd-*` files hold no link rules.

### `.claude/skills/sssf/tests/test_agentsh_guard.py`

Every new test uses the `world` fixture. It builds its config from the shipped `sssf.config.yaml` through `both_harnesses()` and runs for both harnesses. Don't build a test-only `RepoSettings` or config for any new test. Keep the `Rules` emulation and `glob_regex` as they are.

When you're done, the test file must not name `sssf-relative-links`, `sssf-link-targets`, `LINK_FOLDERS` or `relative_secret_forms` anywhere. The check below greps for them.

**Push tests.**
- Add to `test_push_cases_that_run`:
  - `["git", "-c", "alias.st=status", "st"]`,
  - `["git", "-c", "alias.lg=log --oneline", "lg"]`.
- Replace `test_push_alias_values_the_rule_does_not_see` with `test_any_alias_value_naming_push_is_denied`. Parametrize it over these, and assert `push_denied(argv)` for each:
  - `["git", "-c", "ALIAS.p=push", "p"]`,
  - `["git", "-c", "alias.p=PUSH", "p"]`,
  - `["git", "-c", "alias.p=!f(){ git push; }; f", "p"]`,
  - `["git", "-c", "alias.p=!git;push", "p"]`,
  - `["git", "-c", 'alias.p=!git "push"', "p"]`,
  - `["git", "-c", "alias.p=!sh -c 'git push'", "p"]`.
- Delete `test_a_push_in_a_value_in_capitals_still_runs` and `test_a_space_before_push_in_an_alias_value_is_a_known_limit`. Their cases are now in the denied list above.
- Keep these unchanged:
  - `test_an_alias_key_in_another_case_is_still_denied`,
  - `test_the_push_patterns_equal_the_designs_network_policy`,
  - the real-argv tests,
  - the hook-name fixture test.

**Link tests.** Delete:
- `test_relative_links_without_dotdot_are_allowed`
- `test_links_that_point_at_a_denied_file_are_denied`
- `test_links_made_inside_a_git_folder_or_to_a_secret_in_sessions_are_denied`
- `test_a_link_made_directly_in_dot_claude_is_denied_for_claude_only`
- `test_the_rule_order_of_the_new_file_rules`
- `test_secret_names_as_bare_link_targets`
- `test_a_relative_link_made_where_denied_files_sit_beside_it_is_denied`
- `test_a_cc_socks_link_is_denied_for_claude`
- `test_a_link_inside_a_git_dir_not_named_dot_git_is_denied`
- `test_a_wildcard_only_secret_name_does_not_deny_every_link`

Add these, each using `world`:

1. **`test_a_relative_symlink_in_the_workspace_is_denied`.**
   - The targets are `testing`, `./x`, `dir/file`, `../x` and `.env`.
   - For each target, these must give `"default-deny-files"`:
     - `world.rules.link(f"{world.R}/l", target)`,
     - `world.rules.link(f"{world.R}/sub/l", target)`,
     - `world.rules.link(f"{world.S}/x/l", target)`,
     - `world.rules.link("/tmp/l", target)`.
   - Before you write it, confirm that no deny glob in `world`'s policy matches one of these bare targets. The world's `secret_files` give `**/*.pem` and `${PROJECT_ROOT}/cfg/**.key`, and the policy has `**/.env` and `**/.env.*`.
   - If one does match, that target's expected value is the deny that matched, not `default-deny-files`, and the test must still assert a deny.
2. **`test_relative_symlinks_elsewhere_are_denied`.** For each pair below, assert `r.decision(r.link(path, target)) == "deny"`:
   - `(f"{world.G}/x", "config")`
   - `(f"{world.G}/worktrees/id/x", "y")`
   - `(f"{HOME}/.claude/x", "settings.json")`
   - `(f"{HOME}/.claude/projects/x", "y")`
   - `(f"{HOME}/.claude.json.x", ".ssh/id_rsa")`
   - `(f"{HOME}/.pi", ".ssh")`
   - `(f"{HOME}/.cache/uv", "x")`
   - `(world.S, "worktrees")`
   - `("/run/user/1000/cc-socks", "gnupg/S.gpg-agent")`
3. **`test_no_file_rule_allows_a_relative_path`.** Every `allow` rule in `world.rules.rules`, other than `sssf-env-examples`, has only paths that start with `/` or `${`.
   - `sssf-env-examples` (`**/.env.example`, `**/.env.sample`) is on `main` unchanged.
   - This test guards against an item-6 allow coming back.
4. **`test_absolute_links_work_as_on_main`.** Assert:
   - `r.link(f"{world.R}/.venv/bin/python", f"{HOME}/.local/share/uv/python/cpython/bin/python3.12") == "allow"`
   - `r.link(f"{world.R}/l", f"{world.R}/src/a") == "allow"`
   - `r.link(f"{world.R}/l", f"{world.R}/package.json") == "allow"`
   - `r.link(f"{world.S}/x/l", f"{world.S}/x/plan.md") == "allow"`
   - `r.link(f"{world.R}/l", "/etc/passwd") == "sssf-default-deny"`
   - `r.link(f"{world.R}/l", f"{HOME}/.ssh/id_rsa") == "sssf-credentials"`
   - `r.link(f"{world.R}/l", f"{world.R}/.env") == "sssf-secret-files"`
   - `r.link(f"{world.R}/l", f"{world.R}/cfg/a.key") == "sssf-repo-secret-files"`
   - when `world.claude`: `r.link(f"{HOME}/.claude/skills/x", f"{world.R}/y") == "sssf-agent-config"`
5. **`test_the_file_rule_order_matches_main_around_the_new_allows`.** Assert:
   - `order.index("sssf-workspace") == order.index("sssf-git-config") + 1`
   - `order.index("sssf-sessions") == order.index("sssf-git-data") + 1`
   - `order[-2:] == ["sssf-home-read", "sssf-default-deny"]`
   - The set of names is exactly `main`'s file-rule names plus `sssf-sessions`, and plus `sssf-refuse-script` for claude. Compare against a literal list in the test, taken from `git show b307ef3:.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`.

Keep these unchanged:
- `test_the_sessions_folder_is_open_and_the_denies_still_hold_inside_it`
- `test_the_refuse_script_is_readable_by_claude_only`
- `test_links_the_baseline_denied_stay_denied`, or fold it into test 4
- `test_read_puts_the_sessions_folder_in_the_main_checkout`
- the fixture-copy test
- the `null` page tests
- the `command_text` tests

### `.claude/skills/sssf/references/config.md`

In "Running local agents under agentsh", under "More known limits":
- **Delete** the line starting "An alias value that names `push` after a character other than a space…". Those forms are now denied.
- **Add** after the `GIT_CONFIG_COUNT` line:

  "Any alias value naming `push`, in any case, is denied, such as `git -c 'alias.p=!f(){ git push; }; f' p`. The alias pattern's `.*push` also reaches past the alias value into later arguments. So an alias whose value only mentions push, such as `git -c alias.ci='commit -m push' ci`, is denied. So is a command after an alias option that names push anywhere, such as `git -c alias.st=status log --grep=push`. It fails closed, and each denial counts toward the three-refusal stop."
- **Replace** the long line starting "A symlink whose target holds `..` is denied…" with:

  "A symlink with a relative target is denied, wherever it's made. agentsh checks a link's target as the raw string and doesn't resolve it against the link's folder, so the policy can't judge it. It falls to agentsh's own `default-deny-files`, as before. An agent's `ln -s` with a relative target fails. So do `bun add` and `npm install`, because the links in `node_modules/.bin` are relative. An absolute link in an allowed place works, so `uv venv` does. `worktree_setup` runs outside the wrap and isn't affected. Each denial counts as a file denial, not a refusal.

  An absolute target is judged by its text against the other rules. So a target holding `..`, such as `<workspace>/../x`, is allowed whenever its text falls under an allowed path, as before. A link target that passes through an existing symlink is judged by its text too."
- **Add**: "An agent can replace its workspace root with an absolute link to a folder it made. Host git, run in the workspace, would follow it. The after-call guard's swapped-directory check is expected to stop the run, but this hasn't been run under the service."
- Keep the sessions-folder line and the argv-command line.

### `docs/designs/agentsh-local-sandbox.md`

- **Under "The first real runs"**, in the paragraph "**The push rule, third form.**":
  - Replace the pattern list with `['^\S*\s+push(\s|$)', '(?s)^\S*\s+-.*\spush(\s|$)', '(?is)^\S*\s+-.*\salias\.[^=\s]*=.*push']`.
  - Change "34 cases" to "41 cases".
  - Add that any alias value naming `push` is denied, in any case.
- **In the paragraph "Relative links, the refuse script and the sessions folder (built)"**:
  - Rename it to "**The refuse script and the sessions folder (built).**"
  - Replace its link sentences, from "agentsh checks a symlink's own path…" to "…and allowed.", with what's built now:

    "agentsh checks a symlink's own path and its target as the raw string the agent passed, both with `symlink`, so no absolute rule can judge a relative target. SSSF adds no link rule. A relative target falls to `default-deny-files`, as on `main`, and the existing rules judge an absolute target. The relative-link allow tried in 8f8304ae was dropped (see below)."
  - Delete the last two sentences, about alias values the push patterns let through.
  - Keep the `REFUSE_*` sentence.
- **In "What got built"**:
  - Add to the push-case list: `git -c ALIAS.p=push p` and `git -c 'alias.p=!f(){ git push; }; f' p` are denied, and `git -c alias.st=status st` runs.
  - Add a bullet: a relative `ln -s` in the workspace is denied, and `uv venv` works.
- Don't edit the "The chain run for the real-run fixes, 8f8304ae" section.

### Other docs

- `.claude/skills/sssf/` has no CHANGELOG, so add none.
- The `file_rules` and `COMMAND_HEAD` comments are covered above. Change no other docstrings.
- Don't edit `app_docs/`. Item 6 never reached `main`, so no write-up there describes it. The documenter should note that any write-up giving `sssf-no-push`'s alias pattern without `(?is)`, or listing alias values that pass, now describes old behaviour.

## Verification

- **The repo's own test command passes.** It runs `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`, which collects `tests/test_agentsh_guard.py`. With no service on the machine, `test_live_service_is_active` skips as before.
- **The push and file-rule tests assert two things:**
  - the module's `sssf-no-push` list equals `docs/designs/agentsh/sssf-net.yaml`'s,
  - `test_file_rules_equal_the_designs_deny_policy_output` passes, so `docs/designs/agentsh/deny-policy.py` and the module agree.
- **No item-6 name is left in the code or tests.** This command exits 0:

  `bash -c '! grep -rnE "sssf-relative-links|sssf-link-targets|LINK_FOLDERS|relative_secret_forms" .claude/skills/sssf/templates .claude/skills/sssf/tests docs/designs/agentsh/deny-policy.py'`
- **Test the push cases through pytest only.** The repo's text guard refuses any shell command whose text holds a git push spelling, file edits through the shell included. Edit files with the file tools, not with shell one-liners.
- **Nothing may change under `adws/adw_modules/`, `adws/tests/`, `specs/` or `app_docs/`.** The builder reads `git status --porcelain` to confirm this. It isn't a check.

## For the engineer

1. **Run under the service.** After `uv run adws/adw_agentsh_policy.py` and the sudo steps, run a `--worktree` chain and a plain chain in sssf-sandbox. Confirm:
   - the push cases in "What got built",
   - that a relative `ln -s` is denied as a file denial,
   - that `uv venv` works,
   - that Claude Code's own work doesn't make relative symlinks, which would now be denied.

   Also confirm, once, that the swapped-directory check stops a run whose workspace root was replaced with a link.
2. **Still open from earlier specs.** `agentsh_guard.py` isn't in `GUARD_MODULES` in `adws/adw_guard_change.py`, or in `CLAUDE.md`'s list of guard code. Both paths are protected.
