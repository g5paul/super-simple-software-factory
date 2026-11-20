# Let the hub's suite pass in an OpenShell VM

## Goal

Run 4a74c2b9 failed verify in an OpenShell VM for three causes this change fixes:

1. 37 skill tests commit in temp repos and the VM has no global git identity.
2. One chain test needs `rsync`, which the VM image lacks.
3. The skill suite needs about 1450 s in the VM and the hub's cap is 1200 s.

The builder does (1) and (2) and the docs. Cause (3) is an edit to `adws/adw_modules/quality.py`, which is under the protected `adws/` and is not in `guard_builder`'s `writes`, so it is under `## For the engineer`. The request asked for it in the same change; the protected list decides otherwise.

## Baseline

`main` at `7117b72`. Every finding is judged against it. There:

- `containerfile()` in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` installs `git curl ca-certificates python3 xz-utils ripgrep fd-find`. `image_tag(Path("/home/u/repo"), "")` is `localhost/sssf-openshell:3028d837a77a`.
- `.claude/skills/sssf/tests/conftest.py` sets no git identity. Tests get one from the machine's `~/.gitconfig`.
- `test()` in `adws/adw_modules/quality.py` has `timeout_seconds=1200`.

## What the planner measured

All runs were from a copy of the checkout at `7117b72`, with `-m 'not pushes and not host_only'`.

- **The fix works.** `tests/test_adw_tickets.py` and `tests/test_agent_commits.py` with `HOME` at an empty folder: 3 failed, 89 passed. The same with `GIT_CONFIG_GLOBAL` at a file holding only `user.name` and `user.email`: 92 passed.
- **What the engineer's `~/.gitconfig` holds.** `user.name`, `user.email`, and `gh`'s credential helpers for `github.com` and `gist.github.com`. There is no `/etc/gitconfig`. So the only setting the suite takes from it is the identity. No test fetches from or pushes to GitHub, so none needs the helpers.
- **Which tests break when `GIT_CONFIG_GLOBAL` is set.** The 17 files that touch `HOME`, `.gitconfig` or `--global`, plus the VM's failing files, were run with it set (1,455 tests). The only tests that break are ones that give themselves a `HOME` with its own `.gitconfig` and then expect git to use that file as the global config:
  - `tests/test_git_dir_guard.py`, through its autouse `home` fixture. Two tests fail directly: `test_an_included_config_file_is_watched` (an include in `$HOME/.gitconfig` is never read) and `test_a_shared_config_change_names_its_keys` (`git config --global` writes the other file, so the message doesn't name `.gitconfig`). About 30 more run `git config --global`.
  - `tests/test_rollback_no_programs.py`, through its autouse `home` fixture, the same way.
  - Each needs git's global config to be `$HOME/.gitconfig`, so each needs `GIT_CONFIG_GLOBAL` unset. Both pass in the VM today, so unsetting it for them keeps them exactly as at the baseline.
- **Why the file must be per test.** The first run used one file for the whole session. Tests that ran `git config --global` wrote `core.excludesFile`, `core.hooksPath` and `include.path` lines into it, and 36 later tests failed on those. With a clean file each of them passed. So the fixture is function-scoped, and a write by one test never reaches the next.
- **Tests that set `HOME` and are fine.** `real_guard` in `tests/test_failed_call_rollback.py` writes a `.gitconfig` with an identity and never runs `--global`; its tests passed with the variable set. `test_agent_commits.py`, `test_agent_pi.py`, `test_container.py`, `test_host_python.py`, `test_openshell_image.py`, `test_sandbox_extensions.py`, `test_sandbox_openshell.py`, `test_sandbox_remote.py` and `test_secret_files.py` set `HOME` for other reasons and passed. `test_secret_files.py` and `test_rollback_no_programs.py` build their own env with `GIT_CONFIG_GLOBAL=/dev/null` and their own identity, which still wins. Leave all of these alone.
- **The `pushes` and `host_only` tests weren't run** (176 in those files), since the planner runs under agentsh. Verify runs them on the host. If one fails there because it needs `$HOME/.gitconfig` as git's global config, the fix is the same one line as in step 2.
- **The hub's `adws/tests` needs nothing.** Every commit there passes `-c user.name=… -c user.email=…` or sets the repo's own config (`test_guard_change.py`, `test_guard_paths.py`, `test_check_projects.py`, `test_features.py`, `test_levers.py`). In the VM, `chain_test` had one failure, the `rsync` one.

## The way picked for the identity, and why

An autouse, function-scoped fixture in `.claude/skills/sssf/tests/conftest.py` writes a new file with `user.name` and `user.email` and points `GIT_CONFIG_GLOBAL` at it with `monkeypatch.setenv`.

- It is git's global level, so a repo's own `user.name` or `user.email` wins, and so does `-c`. That is what the request needs for `_clone` and for tests that check authorship.
- `GIT_AUTHOR_*` and `GIT_COMMITTER_*` were rejected: they win over a repo's own config.
- `GIT_CONFIG_COUNT` pairs were rejected: they act as `-c`, so they win over a repo's config, and four tests set `GIT_CONFIG_COUNT` themselves.
- A temp `HOME` was rejected: it moves uv's, Claude Code's and pi's folders too.
- `GIT_CONFIG_SYSTEM` was rejected: the engineer's `~/.gitconfig` would still be read on the host, so the host and the VM would still differ.
- `monkeypatch.setenv` changes `os.environ`, so chain scripts the tests start as subprocesses inherit it. `utils.git_location_env` keeps `GIT_CONFIG*` on purpose.

## Steps for the builder

### 1. `.claude/skills/sssf/tests/conftest.py`

Add one fixture after `private_registry`:

```python
TEST_GIT_NAME = "SSSF Test"
TEST_GIT_EMAIL = "sssf-test@example.invalid"


@pytest.fixture(autouse=True)
def git_identity(tmp_path_factory, monkeypatch):
    """Every test gets a git identity that doesn't come from the machine, so a temp
    repo can commit where there is no ~/.gitconfig, as in an OpenShell VM. It is git's
    global config, so a repo's own user.name or user.email still wins. The file is new
    for each test: a test's `git config --global` never reaches the next one. It hides
    the engineer's ~/.gitconfig. A test whose own HOME holds the .gitconfig it means git
    to read asks for this fixture and unsets GIT_CONFIG_GLOBAL."""
    path = tmp_path_factory.mktemp("gitconfig") / "gitconfig"
    path.write_text(f"[user]\n\tname = {TEST_GIT_NAME}\n\temail = {TEST_GIT_EMAIL}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    return path
```

Add two sentences to the module docstring saying the same. Change nothing else in the file.

### 2. The two `home` fixtures

In `.claude/skills/sssf/tests/test_git_dir_guard.py` and `.claude/skills/sssf/tests/test_rollback_no_programs.py`, change the autouse `home` fixture only:

- Add `git_identity` to its parameters, so it runs after the conftest fixture whatever order pytest picks.
- Add `monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)` beside the `XDG_CONFIG_HOME` line.
- Add to its docstring: git's global config is this home's `.gitconfig`, not the suite's test identity.

Change no test body and no assertion in either file.

### 3. New file `.claude/skills/sssf/tests/test_git_identity.py`

Give it the docstring the other test files have, with the run command. A local `git(cwd, *args)` helper runs plain `git` with no `-c` and no `env=`. Tests, in this order:

- `test_a_fresh_repo_commits_with_no_global_config(tmp_path, monkeypatch)`: point `HOME` at a new empty folder; `delenv` `XDG_CONFIG_HOME`, `EMAIL`, `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL`; `git init -q`, `git commit -q --allow-empty -m x`. Assert exit 0 and that `git log -1 --format=%an|%ae|%cn|%ce` is the test name and email twice, read from `conftest.TEST_GIT_NAME` and `TEST_GIT_EMAIL`. At the baseline this fails: with no identity on a machine without a domain name, and on the author check anywhere.
- `test_a_repos_own_identity_wins(tmp_path)`: in a fresh repo set `user.email` to `own@example.com` and leave `user.name` unset. Commit. The author's email is `own@example.com` and the name is the test name. Then set `user.name` to `own` and commit again: both are the repo's.
- `test_a_command_line_identity_wins(tmp_path)`: `git -c user.name=t -c user.email=t@example.com commit` gives `t` and `t@example.com`, as the suite's helpers rely on.
- `test_the_global_config_is_the_tests_own_file(git_identity)`: `os.environ["GIT_CONFIG_GLOBAL"]` is `str(git_identity)`; the file isn't under `Path.home()`; `git config --global --list` prints exactly the two identity lines.
- `test_a_global_write_stays_in_its_test_part_1(tmp_path)` runs `git config --global core.hooksPath /nowhere`, and `…_part_2`, right after it, asserts `git config --global --get core.hooksPath` exits 1. Part 2 passes on its own too.
- `test_a_test_home_takes_its_gitconfig_back(tmp_path, monkeypatch, git_identity)`: do what step 2's fixtures do (own `HOME` with a `.gitconfig` holding `[user] name = t`, `delenv` `GIT_CONFIG_GLOBAL` and `XDG_CONFIG_HOME`). `git config --global user.name` prints `t`, and a `git config --global core.x y` lands in that `.gitconfig` and not in `git_identity`'s file.

No test here needs a marker. None starts an agent, a VM or a gateway.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

In `containerfile()`, the apt line's package list becomes `git curl ca-certificates python3 xz-utils ripgrep fd-find rsync`. That is the whole code change: add ` rsync` after `fd-find`. Don't add `rsync --version` to the last `RUN` line; `apt-get install` already fails the build when a package is missing, and every other line must stay as it is. Don't touch `policy()`, `READ_ONLY_PATHS`, `REGISTRIES` or anything else. The module docstring doesn't list apt packages, so it stays.

### 5. `.claude/skills/sssf/tests/test_sandbox_openshell.py`

- In `test_containerfile_without_a_python_installs_none`, the pinned tag becomes `localhost/sssf-openshell:87ec13f36d5e`, and its comment becomes `# the tag once the image holds rsync`. The planner computed that value from the baseline text with ` rsync` added after `fd-find`. If the built code gives another value, the apt line differs from step 4; fix the line, not the pin.
- Add `test_the_image_holds_rsync_and_nothing_else_changed`, beside `test_containerfile_holds_pi_ripgrep_and_fd`:
  - `text = osh.containerfile(F, "")`; the one line with `apt-get install` ends `" git curl ca-certificates python3 xz-utils ripgrep fd-find rsync && rm -rf /var/lib/apt/lists/*"`.
  - `before = text.replace(" fd-find rsync &&", " fd-find &&")`, `before != text`, and `"localhost/sssf-openshell:" + hashlib.sha256(before.encode()).hexdigest()[:12] == "localhost/sssf-openshell:3028d837a77a"`. That is the baseline's tag, so every other line of the image is as at `7117b72`.
  - `"rsync"` is nowhere in `osh.policy_yaml(F, [])` or in `osh.policy_yaml(F, [], frozenset({"claude_code", "pi"}))`, and `osh.READ_ONLY_PATHS` and `osh.REGISTRIES` equal the baseline's literal values (copy them from the module into the test).
- Change no other test. The existing policy and network-rule tests must pass unedited.

### 6. Docs

- `.claude/skills/sssf/references/config.md`, section "A local sandbox: `--sandbox openshell`", paragraph **What the image holds**: add `rsync` to the list after `fd-find`. Add one sentence after the list: adding `rsync` changed the image's tag, so each repo's next `--sandbox openshell` run after it updates SSSF stops before it starts and names `just openshell-setup`, which builds the new image; the old image stays until removed by hand.
- `docs/designs/openshell-sandbox.md`, the **The image** bullet (about line 97): say the image also holds `rsync`, from apt, which the hub's guard-change chain test needs.
- `docs/open.md`:
  - The row that starts "The hub's suite still fails in an OpenShell VM" keeps causes (4) and (5), so edit it, don't delete it. Keep the first sentences up to "gave the causes". Replace causes (1) and (2) with one sentence: the missing git identity (37 tests) and the missing `rsync` (1 chain test) were fixed by guard run 94e1c6fd. Replace cause (3) with: the cap edit to `quality.py` is the engineer's, in `specs/94e1c6fd_hub-suite-in-vm.md`; until it lands the suite still times out in the VM at 1200 s. Renumber the pi login tests and the four tests that need a closer look as (1) and (2). Keep the three load failures and the obs relay sentences. Add that the proof run after the merge is still owed.
  - The row "A repo can't choose what its OpenShell image holds" lists the fixed tools; add `rsync` after `fd-find`.
- No README, CHANGELOG or help text names the image's packages or the conftest. `docs/sssf.md` describes the conftest's two markers only; leave it.
- For the documenter: no `app_docs/` write-up is made wrong by this, but the write-up for guard run 2656ff74 (VM size and `TMPDIR`) describes the image without `rsync`; note it there if it lists the packages.

## Threat model

The agent runs as the engineer's own user on the host, or as the sandbox user in a VM.

- **The image.** `rsync` becomes one more program in a VM that already has `git`, `curl`, `python3`, `node` and `uv`. It reads and writes only what the sandbox user can, under the same landlock policy, and it reaches only the hosts the unchanged network policy allows, where `curl` and `git` already can. The agent can already copy files in the VM with `cp` or `python3`. The guard can't stop an agent using `rsync` inside the VM and doesn't try to. Nothing comes back from the VM by `rsync`: the host takes commits and bundles through `sandbox_remote`, which this change doesn't touch.
- **The test identity.** It is test code. No shipped module reads `GIT_CONFIG_GLOBAL` to decide anything new. The git-directory guard already watches whatever file git reports as a config origin, including a `GIT_CONFIG_GLOBAL` file, through `git config --list --show-origin`; the planner's run showed it refusing on that file's contents. An agent that runs the suite gets a throwaway global config per test and can't reach the engineer's `~/.gitconfig` through it any more than before.
- **The cap.** A longer cap lets a hung suite hold a run for longer before SSSF stops it. It bounds nothing an agent couldn't already do inside 1200 s.

## Bar

Against `7117b72`:

- **Identity, no global config.** `test_a_fresh_repo_commits_with_no_global_config` passes. At the baseline it fails.
- **Identity, a repo's own wins.** `test_a_repos_own_identity_wins` and `test_a_command_line_identity_wins` pass.
- **`rsync`.** `test_the_image_holds_rsync_and_nothing_else_changed` passes: `rsync` is on the apt line, and the policy, network rules and every other line of the image are as before.
- **Cap.** `test()`'s cap is 2400. This part is the engineer's.
- **Nothing else changes.** Every existing test passes on the host, with no assertion edited except the pinned image tag.
- **Never.** No agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise. No change to `policy()`, to what the VM can reach, or to any module but the one apt line. No test writes to the engineer's real `~/.gitconfig`: with the fixture, a stray `git config --global` in a test now lands in a temp file, which is safer than the baseline.

## Verification

Under agentsh, run from a copy as the build brief says (`D=$(mktemp -d)` and the brief's copy command, then work in `$D`).

From `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_identity.py tests/test_sandbox_openshell.py tests/test_git_dir_guard.py tests/test_rollback_no_programs.py tests/test_failed_call_rollback.py tests/test_adw_tickets.py tests/test_host_agents.py tests/test_agent_commits.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

From the repo root:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests/test_guard_change.py -q -p no:cacheprovider
```

Both exit 0. Don't pass `--basetemp` under a folder named on pytest's own command line: `no_processes_left` then counts pytest itself as a leftover process.

Leave the whole suite to verify. The repo's `test` check runs all of `.claude/skills/sssf/tests`, the new file included, and `chain_test` runs `adws/tests`, so the plan needs no extra check. No test starts a real VM, a real gateway or a real agent.

## Out of scope

Old holes and open items this change leaves as they are:

- `test_rosters.py`'s ChatGPT roster tests, which need a pi login in the VM.
- `test_git_dir_guard.py`'s `[chmod]` case, its two unnameable-dir tests and its stalled-listing timing test, and `test_agentsh_guard.py`'s non-UTF-8 child test, which fail in the VM for causes not yet found.
- Making the suite faster, such as with `pytest-xdist`.
- A repo still can't choose what its image holds; `rsync` joins the fixed list.
- Old image tags and their VM disks stay on disk after the tag changes (the `docs/open.md` cleanup row).
- Claude Code is still installed unpinned in the image.
- The machine's system git config (`/etc/gitconfig`) and `$XDG_CONFIG_HOME/git/ignore` and `attributes` are still read by the suite. The hub's host has none.
- `real_guard` in `tests/test_failed_call_rollback.py` and the other tests that set `HOME` keep running with the suite's `GIT_CONFIG_GLOBAL`; they pass that way and aren't changed.
- The template's `quality.py` isn't touched.

## For the engineer

These paths are protected and `guard_builder`'s `writes` doesn't name them.

1. **The cap, in `adws/adw_modules/quality.py`.** In `test()`, change `timeout_seconds=1200` to `timeout_seconds=2400` and replace the comment above `argv` with:

   ```python
   # The hub suite, the command each test file's docstring gives, run from
   # the skill folder. On the host it takes about 740 s (3,858 tests, 5.2 a
   # second, 2026-11-20). In an OpenShell VM (4 vCPUs, 4 GiB) it ran 2.7 a
   # second, about 1450 s for the whole suite, and timed out at the old
   # 1200 s cap (run 4a74c2b9). The cap only has to catch a hang, so it is
   # 2400 s: the VM's time with about two thirds over.
   ```

2. **A test for the cap**, if wanted, in `adws/tests/`: parse `adws/adw_modules/quality.py` with `ast` and assert the `timeout_seconds` keyword in `test()` is 2400. It can't pass before edit 1, so it isn't a builder step.
3. **`docs/open.md`**: once edit 1 is in, drop the sentence in the VM row that says the cap edit is owed.
4. **After the merge**, as the request says: `install.py --update` for the hub's own install (which carries the new `sandbox_openshell.py` into `adws/adw_modules/`), `just openshell-setup` to build image `87ec13f36d5e`'s counterpart for this repo's folder and Python, commit the update, then rerun `specs/tasks/flaky-obs-retry-timing.md` with `just simple-sdlc --worktree --sandbox openshell`. Expect `test` to finish inside 2400 s with only the out-of-scope failures, and `chain_test` to pass.
