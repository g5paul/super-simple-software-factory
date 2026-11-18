# Put bun in the OpenShell image, and name a missing check tool before any agent starts

Run it with `--worktree` and `--base main`, on the hub's own config. The image recipe lives in `sandbox_openshell.py`, and the tool check in `sandbox_remote.py`. Both are guard code, so this goes through the chain.

## What broke

On 2026-11-18 a project on `sandbox: openshell` cloned its 21 MB bundle into the VM, which is fixed as of run 459e0656. Plan and build then went through. At verify_1, the two quality checks that run `bun test` and `bun build` both exited 127, because the VM has no `bun`. The builder in fix_1 rightly reported that no source change could fix that. The run cost $0.25 and stopped with a correct edit left in `sandbox-uncommitted.patch`.

`containerfile()` in `sandbox_openshell.py` installs node, uv, Claude Code and pi, and no bun. A repo has no way to add a tool. E2B's template (`scripts/e2b_template.py`) already installs bun. Of the five repos SSSF runs in, two call `bun` in `quality.py`. One of those also runs `bun install` in `worktree_setup`, so it would fail even earlier, at setup.

The tool check (`TOOLS`, `tools_needed`, `judge_tools` in `sandbox_remote.py`) covers what the roster's agents need: git, uv, claude, node and pi. It knows nothing of the repo's own check and setup commands. So the run spent money on a plan and a build before finding out it could never pass verify.

## The change

1. **bun in the OpenShell image, pinned.**
   - The Containerfile installs bun at a fixed version, held in a constant beside `NODE`.
   - Use the host's bun, 1.4.4, unless the spec finds a reason for another.
   - Install it from a source that names the version: the npm package `bun@<version>`, or bun's install script with its version argument. The spec says which, and why it's no looser than the node and uv lines.
   - The bun binary must be on the sandbox user's `PATH`, executable by that user, and not writable by them.
   - The recipe's closing version line prints `bun --version`, so a broken install fails the build.
   - The new recipe changes every repo's image tag, and that's expected.
2. **A missing check or setup tool stops the run in its `sandbox` phase, before any agent starts.**
   - The tool check also looks up, in the VM, the first word of each command the repo's `quality.py` checks and `worktree_setup` run.
   - A command name that isn't on the VM's `PATH` is a problem that stops the run with a message naming it, the way a missing `claude` does today. One that's found is logged in `sandbox_tools`.
   - The spec finds how SSSF reads `quality.py`'s commands today, without running the repo's code on the host beyond what SSSF already runs. It also says how it handles a placeholder check, a shell builtin, a path to a script in the repo, and a command written as a shell string.
   - If a command can't be judged that way, the check skips it and logs the skip. It never guesses a stop.
   - Each command name goes to the VM as data, never spliced into a shell line. Use `command -v` with the name as an argument, or the like, and say how a name with shell metacharacters is handled.
   - This applies to every provider, since Vercel's image may lack bun too. Vercel and E2B keep their images. A missing tool there now stops the run before the agents instead of at verify.
3. **Docs.**
   - `config.md`'s OpenShell section lists what the image holds, bun included, and says that a check or setup command missing from the VM stops the run in its `sandbox` phase.
   - `openshell-sandbox.md`'s "As built" says the same.
   - The `docs/open.md` row this run adds names the general fix: a repo-chosen tool list for the image, which would be a new mechanism.

## The bar

- **The image has bun.** `containerfile()` holds a pinned bun install and `bun --version` in its version line. The test asserts the pin, not just the word `bun`.
- **A missing tool stops the run early, at $0.** Through `tests/test_sandbox_openshell.py`'s fake CLI, a repo whose `quality.py` runs a command missing from the fake VM stops in its `sandbox` phase, naming the command, before any agent starts. The same goes for a `worktree_setup` command. A repo whose commands are all present opens as on `main`.
- **No false stop.** These open as on `main`:
  - a placeholder `echo` check;
  - a shell builtin;
  - a script path inside the repo;
  - a command the check can't parse.

  The shipped `templates/adws/adw_modules/quality.py` with its placeholders opens too.
- **Nothing runs from the repo on the host that didn't before.** The new check reads commands the way SSSF already does, or as plain text. A probe `quality.py` whose import has a side effect, such as writing a marker file, shows no new run of it on the host compared with `main`.
- **No injection.** A check command whose first word holds `;`, `$(...)` or a newline can't run anything in the VM beyond the lookup. The test asserts what the fake VM was asked to run.
- **Everything else unchanged.** Every existing test passes. An OpenShell run's trace events match `main`'s, plus the tool rows. Vercel and E2B commands match `main`'s, plus the lookup.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. No test starts a real VM or builds an image with Podman. Keep runs light, since the agentsh event store slows under heavy load (`docs/open.md`).

## Out of scope

- A repo-chosen tool list or Containerfile lines in `repo.yaml`. Add an open.md row for it.
- Building any repo's image. The engineer runs `just openshell-setup` in each repo on OpenShell after the update.
- The switch steps and install step 12 in the cookbooks. The hub session edits those on `main` after this run.
- The P2 row on committed secret files and the other open rows.
