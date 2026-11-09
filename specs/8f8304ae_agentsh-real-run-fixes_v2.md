# Fix what the first real runs under agentsh found

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md`. Source task: `specs/tasks/guard-agentsh-wrap-4.md`. The earlier tasks (`specs/tasks/guard-agentsh-wrap.md` to `-3.md`) still stand except where this changes them. The engineer's decisions in the design doc's "The first real runs" section are settled. Don't reopen them.

Revision 2. It answers spec review 1:
- Bar 1 is narrowed to a named list.
- The alias-value forms that now pass are named as a limit.
- Bar 6 uses the brief's "points at", with a `${HOME}/.claude/*` link deny and `.git` link-path tests.

## Threat model

Each local Claude Code or pi agent runs as the engineer's own user under `agentsh wrap`, with a default-deny policy that SSSF generates. The agent can:
- run any command,
- write any file in its workspace,
- make links,
- make SSSF on the host read files it wrote, such as `plan.md` in the handoff folder. SSSF reads those unwrapped, so a link there gets followed outside the policy.

The guard sees only what the ptrace tracer reports:
- execve argv, joined by spaces with `argv[0]` included,
- file operations on resolved paths,
- connects.

A `symlinkat` is different. The engine checks the link's own path, resolved, and also checks its target as the raw string the agent passed, with no resolving (`internal/ptrace/handle_file.go`, `SYS_SYMLINKAT`, fork at 41960715). Both checks use operation `symlink`, and both must allow.

At this level the guard can't stop:
- a process that leaves the wrap,
- a copied binary under a new name,
- what host-side tools do with files the agent left behind,
- quality checks, which run outside the wrap.

The text guard (`rules.py`, `sssf_refuse.py`) stays on and is unchanged.

## Baseline

`main` at `6530141`. Every finding is judged against that commit. The wrap itself merged as b5d0b82.

## Bar

Must do, against the baseline:
1. **The push rule.**
   - The five real Claude Code git argv lists in `docs/designs/agentsh/first-real-runs/claude-code-git-argv.json` are not denied by `sssf-no-push`. Neither is `git -C /x/push-tools status`.
   - Every case in the existing `test_push_cases_that_are_denied` list stays denied: `git push`, `git -c a=b push`, `git -c "a=b c" push`, `git -C /x push`, `git --git-dir /x push`, `git -c alias.p=push p`, `git -c a=b commit -m push`, `"" push`, `git -c $'a=b\nc' push` and `git -C $'/x\ny' push`.
   - `git -c 'alias.p=!git push' p` is also denied.
   - This bar doesn't claim that every form the baseline denied stays denied. The brief's patterns let some alias values through. See Out of scope.
2. On a `--worktree` run, a Claude Code agent can read the refuse script, `<main>/adws/adw_modules/sssf_refuse.py`.
3. A `null` events page means no rows. These still count as a failed poll:
   - an HTTP error,
   - a refused connection,
   - invalid JSON,
   - a body that is not `null`, a list or an object.
4. A counted exec denial names its command from `fields.argv` when the event has no `command`, shortened. This shows in the RuleBreach message and in the trace.
5. On every run, agents can read and write `<main>/<data_dir>/sessions` and everything under it. The rule comes after every deny rule, so these stay denied inside it:
   - `.env` and other secret files,
   - credentials,
   - `/proc` secrets,
   - the other agent's sign-in,
   - `.git/hooks` and `.git/config`.
6. A relative symlink made in the workspace is allowed when its target has no `..` and doesn't point at a denied file. A link that points at a denied file stays denied, with a relative or an absolute target. Concretely, all of these are denied:
   - a target naming a secret file (`.env`, `.env.*`, the repo's `secret_files` names),
   - any target holding `..`,
   - any link whose path or target lies in a `.git` folder, so `<git_dir>/x -> config` is denied,
   - in the claude policy, any link made directly in `${HOME}/.claude/`, so `~/.claude/x -> settings.json` is denied.

Must never do, against the baseline:
- keep an agent file,
- run agent code,
- delete or overwrite an engineer file,
- allow a path the baseline denies, beyond items 2, 5 and 6,
- let a run stop because the API answered normally.

A real Claude Code call that the policy denies, with no agent action behind it, is a false positive.

Accepted, not findings:
- the item-5 allow on every run's handoff folder,
- the relative-symlink limits named below,
- the alias-value forms named below, which the engineer's chosen patterns let through,
- every limit `config.md` already lists.

## Out of scope

These holes stay open. File them as old holes:
- **Alias values the new push patterns let through.** The baseline's `-.*push` denied these, and the brief's three patterns don't:
  - an alias whose value names `push` after a character other than a space, or inside a word: `git -c 'alias.p=!f(){ git push; }; f' p`, `git -c 'alias.p=!git "push"' p`, `git -c 'alias.p=!git;push' p`,
  - an alias value holding a space before `push` where the `\S*` can't reach.
  The network rules refuse GitHub. The engineer chose these patterns, so the change can't make them stricter.
- Moving the handoff folder into the worktree, which is the stronger fix for item 5.
- pi under the wrap. That includes pi's `sssf_rules.ts` extension, which a `--worktree` pi run loads from the main checkout. The same failure as item 2 is likely there, but pi needs its own policy and a real run first.
- Running a real agent under the service, sudo, patching the agentsh fork, and turning this on outside sssf-sandbox.
- **A link target with `..`.** The engine can't place it, because it doesn't resolve the target against the link's folder. It stays denied: see item 6.
- **A link target that passes through a symlink already in the tree that leads out.** The baseline has the same gap for absolute targets.
- **Glob metacharacters in the main checkout's path.** `rule()` doesn't escape them. The git dir already has this gap.
- The install script refusing a template that differs from the committed version. That's the chain run after step 2 (design doc, Next 4).
- Agents reading other runs' handoff files. The engineer accepted this.

## Findings that shaped the plan

- **`REFUSE_*` files.** No file by that name exists in the templates. `grep -rn "REFUSE_" .claude/skills/sssf/templates` finds only:
  - `SSSF_REFUSE_RULES`, an environment variable,
  - `REFUSE_SHELL` and `REFUSE_EXTENSION`, which are the script paths.

  So a worktree Claude run needs only two paths in the main checkout:
  - `<main>/adws/adw_modules/sssf_refuse.py`, which `agent_cc.REFUSE_SHELL` sets as `CLAUDE_CODE_SHELL_PREFIX`,
  - `<data_dir>/sessions/**`, which holds `context_handoff/`, `quality/` and so on.

  The main checkout's `.claude/` is already readable through `sssf-main-config`. Run ac34222b reached the plan gates with only those two missing, which confirms the list.
- **Relative symlinks (item 6).** In the fork at 41960715, `extractFileArgs` returns `(linkpath, target)` for `SYS_SYMLINKAT`, and the target is the raw string. `internal/api/ptrace_handlers.go` then runs `CheckFile(Path, "symlink")` and `CheckFile(Path2, "symlink")` and merges the two decisions. `CheckFile` (`internal/policy/engine.go`) compiles every glob with separator `/` (gobwas). When no rule matches a write-class operation, it returns `default-deny-files`. SSSF's rules are all absolute, and the last is `/**`, so a relative target like `testing` matches none of them. That explains the denial: the target is checked as a path of its own, as raw text.
  - The policy can fix this for targets with no `..`. Such a target lands in the link's folder or below it, and the link's own path is checked by the same rules.
  - It can't fix targets with `..`, because their meaning depends on the link's folder, which the engine doesn't use.
  - The folders where an agent may make links and a denied file sits beside them need their own rules: the workspace and sessions folder (secret names), any `.git` folder (hooks, config), and `${HOME}/.claude/` (agent config). Every other folder where links are allowed holds no denied file. Those are temp, tool caches, `${HOME}/.pi` and `${HOME}/.claude/**` below the config folders. The config subfolders, such as `skills/**` and `hooks/**`, already deny `symlink` on the link path through `sssf-agent-config`.
- **An old hole the same rule closes.** An absolute target holding `..`, such as `${PROJECT_ROOT}/../../.ssh/id_rsa`, is matched as raw text. It hits `sssf-workspace`, so the baseline allows it. The new `..` deny closes it.

## Files and changes

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

1. **Push rule (item 1).** In `COMMAND_HEAD`, change `sssf-no-push`'s `args_patterns` to exactly this text, as `docs/designs/agentsh/sssf-net.yaml` holds it:

   `['^\S*\s+push(\s|$)', '(?s)^\S*\s+-.*\spush(\s|$)', '(?s)^\S*\s+-.*\salias\.[^=\s]*=\S*push']`

   Keep the Python string escapes: the file writes `\\S` inside a normal string. Update the comment above it to say three things:
   - `push` must be a word of its own after a leading option, or appear in an alias's value up to its first space.
   - A hook name such as `hook.pre-push.enabled` or a path such as `/x/push-tools` no longer matches.
   - Alias values that name `push` after a non-space character pass, and the network rules cover them.

2. **Null page (item 3).** In `AgentshProcess._get`:
   - `data is None` returns `[]`.
   - A dict uses `data.get("events")`, and a `None` there also returns `[]`.
   - A list is used as is.
   - Anything else (str, int, float, bool) raises `ValueError("unexpected events body")`, so the poll counts as failed. Don't let a string iterate quietly into an empty list.
   - A rows value inside a dict that isn't a list also raises `ValueError`.
   - Keep the `isinstance(r, dict)` filter.

3. **Command from argv (item 4).** Add a module function `command_text(event: dict) -> str`:
   - Use `event["command"]` or `fields["command"]` when it is a non-empty string.
   - Otherwise, if `fields["argv"]` (or `event["argv"]`) is a list of strings, join it as `basename(argv[0])` followed by `argv[1:]`, separated by spaces.
   - Write `\n`, `\r` and `\t` inside the text as the two-character escapes.
   - If the result is longer than `COMMAND_CHARS = 160`, return the first word, then ` … `, then the last 140 characters cut forward to the next space. For the fixture event this gives something like `git … -c hook.post-index-change.enabled=false config user.name`, which names the command.
   - Return `""` when there's nothing to use.

   In `_take`, use `command_text(event)` for `self.last` and for the `path` fallback in `other_denials`. Update the comment on `last` to say it holds the shortened command.

4. **File rules (items 2, 5, 6).** The new signature is `file_rules(git_dir, agent, secret_files=(), *, sessions: str, refuse_script: str)`. Pass both new arguments through `policy_text(settings, git_common_dir, harness, name, *, sessions: str, refuse_script: str)`. They're required keywords, so no caller gets a silent default. Add these rules:

   - **`sssf-link-targets`**: decision `deny`, operations `['symlink']`. Place it right after `sssf-git-config`, so it's the last deny before any allow. The engine checks the link path and the raw target against it with op `symlink`, so each glob below catches either end. Its paths, in order:
     1. `**..**`: any target holding `..`, absolute or relative.
     2. The bare forms of every secret name, from a helper `relative_secret_forms(globs) -> list[str]` over `SECRETS` and each `secret_glob(p)` for the repo's `secret_files`, with duplicates dropped in order:
        - For a glob `**/X`, add `X`. `**/X` already denies `sub/X` and absolute paths for all ops through its own rule.
        - For a rooted `${PROJECT_ROOT}/.../last`, add `last` and `**/last`. A rooted secret is then denied as a link target by its name from any folder. That fails closed.
     3. `.git`, `.git/**`, `**/.git`, `**/.git/**`. `**/.git/**` also matches a link *path* inside any `.git` folder. That includes the common git dir `<main>/.git/...` and a worktree's `<main>/.git/worktrees/<id>/...`, so `<git_dir>/x -> config` and `<git_dir>/x -> hooks/pre-commit` are denied. Git doesn't make symlinks inside `.git` in normal use, so this fails closed at most.
     4. **claude policy only**: `${HOME}/.claude/*`. This denies any link made directly in `~/.claude/`, where `settings.json`, `settings.local.json`, `CLAUDE.md` and `AGENTS.md` sit, so `~/.claude/x -> settings.json` is denied. The config subfolders already deny links through `sssf-agent-config`. Don't add bare `CLAUDE.md`-style names: they would refuse workspace links such as `AGENTS.md -> CLAUDE.md`.

     Credentials, `/proc` secrets and sign-ins are absolute under `${HOME}` or `/proc`. A relative target reaches them only through `..`, which is denied, or through an existing link, which is out of scope. Their raw absolute forms already hit their own deny rules.
   - **`sssf-sessions`**: decision `allow`, operations `ALL`, paths `[sessions, f"{sessions}/**"]`. Place it right after `sssf-git-data`, after every deny rule. Comment it: "A `--worktree` run's handoff folder sits in the main checkout. Every run's, decided 2026-11-09."
   - **`sssf-refuse-script`**: decision `allow`, operations `READ`, paths `[refuse_script]`. Place it right after `sssf-sessions`, and only when `agent == "claude"`. pi's policy doesn't get it (out of scope).
   - **`sssf-relative-links`**: decision `allow`, operations `['symlink']`, paths `['[!/]**']`. It matches any target that doesn't start with `/`. Place it right before `sssf-default-deny`. The `sssf-link-targets` denies above decide which relative targets are refused. The link's own path still needs an earlier allow, such as the workspace, the sessions folder or temp.

5. **Paths in `read(cfg)`.**
   - **`sessions`**: let `d = Path(cfg.defaults.data_dir)`; the shipped default is `adws/adw_data`.
     - If `d` is absolute, `sessions = str((d / "sessions").resolve())`.
     - Otherwise `sessions = str((root / d / "sessions").resolve())`, where `root` is the main checkout `read` already works out.

     Read `data_dir`, not `data_root`. `worktree.anchor` pins `data_root` from `data_dir` (`worktree.py:87`), and `read` runs before `anchor` in `session.ensure` but after it in `adw_agentsh_policy.py`. Reading `data_dir` gives the same text both times.
   - **`refuse_script`**: `str(Path(__file__).resolve().with_name("sssf_refuse.py"))`. It's the same file as `agent_cc.REFUSE_SHELL`, resolved because the tracer reports resolved paths. Don't import `agent_cc`.

   Pass both to `policy_text`. The installed policy and `check_machine`'s comparison both come from `read`, so they agree.

### `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py`

It calls `agentsh_guard.read(cfg)`, so it should need no change. Check that nothing else calls `policy_text` or `file_rules` (grep the templates and the tests), and update any caller you find.

### `docs/designs/agentsh/deny-policy.py`

`test_file_rules_equal_the_designs_deny_policy_output` compares this script's `file_rules:` section with ours, so the script needs the same changes:
- Add the same four rules in the same places, including the claude-only `${HOME}/.claude/*` path and the claude-only refuse rule.
- It takes `<git_dir> <out_dir>`. Derive these from them:
  - `main` as the parent of `git_dir`,
  - `sessions = f"{main}/adws/adw_data/sessions"`,
  - `refuse = f"{main}/adws/adw_modules/sssf_refuse.py"`.

The test passes the same two values to `policy_text`.

Check how `docs/designs/agentsh/sssf-dd-claude.yaml` and `sssf-dd-pi.yaml` were made before you touch them. Regenerate them only if they're the script's committed output for the `/m/proj/.git` example. Leave them alone if they came from a real path.

### `.claude/skills/sssf/tests/fixtures/agentsh-first-real-runs/`

Copy the three files from `docs/designs/agentsh/first-real-runs/` byte for byte with `cp`, so the tests run where `docs/` isn't installed:
- `claude-code-git-argv.json`
- `events-page-past-end.json`
- `execve-denial-event.json`

### `.claude/skills/sssf/references/config.md`

In "Running local agents under agentsh":
- Replace the limit "A repo whose path holds `push` has every `git -C <path>` denied." with: "A `git -C` path, or any option value, holding `push` as a separate word after a space is denied. It fails closed."
- Keep the multi-line message limit, and say the denied case is `push` as its own word.
- Add a limit: "An alias value that names `push` after a character other than a space, such as `git -c 'alias.p=!f(){ git push; }; f' p` or `git -c 'alias.p=!git \"push\"' p`, reaches exec without the word where the rule looks. The network rules refuse GitHub."
- Add a paragraph on `--worktree` runs. Under agentsh, agents may read and write the main checkout's `<data_dir>/sessions/`, which holds every run's handoff folder. Claude Code agents may read `adws/adw_modules/sssf_refuse.py`. Secret files and git config stay denied inside the sessions folder.
- Add a limit: "A symlink whose target holds `..` is denied, even inside the workspace, because agentsh checks a link's target as written and doesn't resolve it from the link's folder. These are denied too:
  - a link whose target names a secret file by its name, such as `.env`, from any folder,
  - a link made in or pointing into a `.git` folder,
  - a link made directly in `~/.claude/` by a Claude Code agent.

  Each fails closed and counts as a file denial, not a refusal. A link target that passes through an existing symlink is judged by its text."
- Mention that a denied exec now names its command from argv, shortened.

### `docs/designs/agentsh-local-sandbox.md`

Under "The first real runs", add a short paragraph that records:
- the item-6 finding: the target is checked raw, relative targets without `..` are now allowed except those pointing at a denied file, and `..` stays denied, with the reason,
- the absolute-`..` hole the same rule closes,
- that no `REFUSE_*` files exist, so the main-checkout allows are the sessions folder and `sssf_refuse.py`,
- the alias-value forms the new push patterns let through.

In "What got built", replace the push-case list for the real-service check. Add `git -C /x/push-tools status` (runs) and `git -c 'alias.p=!git push' p` (denied).

### Other docs

- Update the comments in `agentsh_guard.py` as described above.
- Check `ls .claude/skills/sssf/` for a CHANGELOG, and add an entry only if one exists.
- Don't edit `app_docs/`. The documenter should note that any earlier write-up describing `sssf-no-push` as `-.*push`, or the agentsh file rules without the sessions allow, now describes old behaviour.

## Tests

All tests go in `.claude/skills/sssf/tests/test_agentsh_guard.py`. Build the config from the shipped `sssf.config.yaml` as the file already does, with no test-only config. Load fixtures from `tests/fixtures/agentsh-first-real-runs/` through a `FIXTURES` path constant.

1. **The real argv lists.**
   - Parametrize over the five lists in `claude-code-git-argv.json` and assert `not push_denied(argv)`. Use the existing `push_denied`, which joins with spaces and reads the patterns from the generated policy text.
   - Assert that one list holds `hook.pre-push.enabled=false`, so the fixture keeps the case that broke the baseline.
2. **Push cases.**
   - Keep every existing case in both lists.
   - Add to denied: `["git", "-c", "alias.p=!git push", "p"]`.
   - Add to runs: `["git", "-C", "/x/push-tools", "status"]` and `["git", "-c", "hook.pre-push.enabled=false", "status"]`.
   - Add a test named for the limit, `test_push_alias_values_the_rule_does_not_see`, that pins `["git", "-c", "alias.p=!f(){ git push; }; f", "p"]` as not denied. A future change to the rule then shows up as a changed limit.
   - Assert that the generated pattern list equals the three strings in `docs/designs/agentsh/sssf-net.yaml`'s `sssf-no-push`. Skip this one assertion when `docs/` isn't installed, as `DENY_POLICY` does.
3. **Null page.**
   - The `Service` fake returns the raw bytes of `events-page-past-end.json` (`null`) for a page past the end. With `POLL_SECONDS` monkeypatched small, run a session for more than three poll intervals. Assert `process.failure == ""` and that the run didn't stop.
   - Call `_get` directly. Assert `[]` for the bodies `null`, `{"events": null}` and `[]`.
   - Assert that `_get` raises for the bodies `"x"` and `42`, for invalid JSON and for an HTTP 500.
   - Extend the existing test so three such failures in a row still set "stopped answering event polls".
4. **An exec event with argv only.**
   - Feed `execve-denial-event.json` (with `fields.argv`, `fields.rule` and `fields.decision`, and no `command`) through `_take` three times with distinct ids.
   - Assert that `process.last[1]` starts with `git`, ends with `config user.name`, and is at most 160 characters plus the ellipsis.
   - Assert that the `RuleBreach` message from `breach()` contains `config user.name` and doesn't contain ``last `` `` (an empty name).
   - Unit-test `command_text` on:
     - a short argv, which comes back unchanged,
     - a `command` field, which wins over argv,
     - an argv holding `\n`, which comes back escaped,
     - an event with neither, which gives `""`.
5. **File rules.**

   Write a helper `first_match(text, path, op, *, home, root)`. It parses the generated YAML, expands `${HOME}` and `${PROJECT_ROOT}` to the given values, and walks `file_rules` in order. Translate each gobwas-style glob to a Python regex:
   - `**` → `.*`, `*` → `[^/]*`, `?` → `[^/]`,
   - `[!x]` → `[^x]`,
   - `{a,b}` → `(?:a|b)`,
   - `\c` → literal `c`.

   The helper returns the rule name. When nothing matches and the op isn't a read, it returns `default-deny-files`. A helper `link(link_path, target)` returns the first deny among `first_match(link_path, "symlink")` and `first_match(target, "symlink")`, or `allow` when both allow. That's how the engine merges the two checks.

   Build the claude and pi policies through `read(cfg)` on a temp git repo with `secret_files: ["*.pem", "./cfg/*.key"]`. Let `R` be that repo's root, `S` its sessions folder, `G` its git dir and `H = /home/u`. Assert:

   - **Sessions**
     - `S/x/context_handoff/plan.md` gives `sssf-sessions` for `write`, `create` and `read`.
     - `S/x/.env` and `S/x/.env.local` give `sssf-secret-files`.
     - `S/x/a.pem` gives `sssf-repo-secret-files`.
     - `H/.ssh/id_rsa` stays `sssf-credentials`.
     - `G/hooks/pre-commit` and `G/config` give `sssf-git-config` for `write`.
     - `sssf-sessions` comes after every rule whose decision is `deny`, except `sssf-default-deny`.
   - **Refuse script**
     - In the claude policy, `read` on the refuse script path gives `sssf-refuse-script`.
     - `write` on it doesn't give `sssf-refuse-script`.
     - The pi policy has no `sssf-refuse-script`.
   - **Links allowed, both policies**
     - `link(R/l, "testing")`
     - `link(R/sub/l, "dir/file")`
     - `link(R/l, "./x")`
     - `link(S/x/l, "plan.md")`
     - `link("/tmp/l", "x")`
   - **Links denied by `sssf-link-targets`**
     - `link(R/l, t)` for each `t` in `../x`, `a/../../b`, `.env`, `sub/.env`, `.env.local`, `a.pem`, `x/a.pem`, `a.key`, `.git/hooks/pre-commit`, and `R + "/../../home/u/.ssh/id_rsa"` (the absolute `..` case).
     - `link(G/x, "config")` and `link(G/x, "hooks/pre-commit")`. The link path inside `.git` is what denies these.
     - `link(S/x/l, ".env")`.
     - In the claude policy only, `link(H/.claude/x, "settings.json")` and `first_match(H/.claude/x, "symlink")` give `sssf-link-targets`.
   - **Links unchanged from the baseline**
     - Target `/etc/passwd` gives `sssf-default-deny`.
     - Target `H/.ssh/id_rsa` gives `sssf-credentials`.
     - `link(H/.claude/skills/x, "y")` gives `sssf-agent-config` in the claude policy.
   - **Order.** Extend `test_rule_order_in_the_generated_policy`:
     - `sssf-link-targets` comes after `sssf-git-config` and before `sssf-workspace`.
     - `sssf-relative-links` is second to last.
     - `sssf-sessions` comes after `sssf-git-data`.
     - `${HOME}/.claude/*` is in `sssf-link-targets` for claude and not for pi.
6. **Paths.**
   - `read(cfg)` on a temp repo gives a `sessions` equal to `<repo>/adws/adw_data/sessions`, resolved, for the shipped `data_dir`.
   - `read(cfg)` returns the same text after `worktree.anchor(cfg, root)`, which proves the install script and `session.ensure` agree.
   - From inside a linked worktree of that repo, the path still points at the main checkout.
7. **Fixture copies.** When `docs/designs/agentsh/first-real-runs/` exists, each copy is byte-equal to its source.

Update the callers of `policy_text` in the existing tests (`test_rule_order…`, `test_a_quote…`, `test_file_rules_equal…` and `push_patterns`) to pass `sessions=` and `refuse_script=`.

## Verification

- The repo's own test command passes. `adws/adw_modules/quality.py` runs `pytest tests` from `.claude/skills/sssf`, which collects `test_agentsh_guard.py`. Run it that way from that folder, then run `uv run pytest tests/test_agentsh_guard.py -q` there for a focused pass. Both must exit 0 on a machine without the service, where `test_live_service_is_active` skips as before.
- Check the push rule with `grep -n "push(" .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`, which should show three patterns. They should match `docs/designs/agentsh/sssf-net.yaml`'s `sssf-no-push` once parsed from YAML, which test 2 asserts.
- `python3 docs/designs/agentsh/deny-policy.py /m/proj/.git "$(mktemp -d)"` exits 0, and its output matches `policy_text`. `test_file_rules_equal_the_designs_deny_policy_output` asserts that.
- Check that no file changed under `adws/adw_modules/`, `adws/tests/`, `specs/` or `app_docs/`. The builder reads `git status --porcelain` for this. It isn't run as a check.

## For the engineer

These need you, outside the builder's reach:

1. **Check the new globs on the fork's engine before the sudo install.** The builder has no Go toolchain, so its test uses a Python emulation. Use `LoadFromFile` and `CheckFile` at 41960715, separator `/`, and confirm:
   - `[!/]**` matches `testing` and not `/etc/passwd`,
   - `**..**` matches `../x` and `/a/../b`,
   - `.env.*` matches `.env.local`,
   - `**/.git/**` matches `/m/proj/.git/x`,
   - `${HOME}/.claude/*` matches `/home/u/.claude/x` and not `/home/u/.claude/skills/x`.

   If `[!/]` doesn't parse there as a negated class, the allow fails closed and relative links stay denied. Record that in `config.md` as a limit rather than widening the rule.
2. **Run both chains under the service.** After `uv run adws/adw_agentsh_policy.py` and the sudo steps, run a `--worktree` chain and a plain chain in sssf-sandbox. Check the trace for:
   - no `sssf-no-push` denial on Claude Code's own git calls,
   - no denial on `sssf_refuse.py`, its folder `adws/adw_modules`, or `adws/adw_data/sessions/<id>/`,
   - no "stopped answering event polls" on an idle agent,
   - no `sssf-link-targets` denial from Claude Code's own work in `~/.claude/` or `.git`,
   - the push cases in the design doc's "What got built" list.
3. **Widen the refuse-script rule if Python needs it.** If Python's start-up stats on `<main>/adws` or `<main>/adws/adw_modules` show up as denials that break the refuse prefix, widen `sssf-refuse-script` to `stat` on those two folders, through a chain run.
4. **Still open from earlier specs.** Add `agentsh_guard.py` to `GUARD_MODULES` in `adws/adw_guard_change.py` and to `CLAUDE.md`'s guard code list, if that hasn't been done.
