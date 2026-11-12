# Close the holes left in the factory-files guard

This run continues on branch `guard-factory-files` at 6472edf, the commit run 37d69462 landed, with `--base main`. That run carried out [guard-factory-files.md](guard-factory-files.md): a fixed list of factory paths in `permissions.py` that every agent is refused unless its own `writes` names them, and an always-writable grant narrowed from all of `data_dir` to the runtime. Its spec is `specs/37d69462_factory-files-guard_v3.md`, and its bar stands.

Its bypass review found nothing blocking. But the round-1 report and a second review by the hub's `bypass-reviewer` subagent found ways to change a fixed path, or code a fixed path loads, that both `main` and 6472edf let through. This run closes the ones the after-call check can catch.

## The problem

`permissions.snapshot` decides what an agent changed. It reads `git diff HEAD --numstat` and `git ls-files --others --exclude-standard`, and five things about that let a change past it.

1. **Renames.** `diff.renames` is on, so a moved file shows as `{src/fixtures => .claude}/settings.json` and matches no protected entry. An agent can swap the reviewer prompt: `git rm --cached` the prompt, `git add` a copy elsewhere as the rename partner, write its own text at the prompt path, and gitignore it.
2. **Quoted names.** `core.quotePath` makes git print a non-ASCII name in quotes, as `".claude/skills/sssf/\303\251.md"`, and the leading quote defeats every prefix match.
3. **Index flags.** After `git update-index --skip-worktree justfile` or `--assume-unchanged`, an edit to the file leaves `git diff` empty. A `writes: []` reviewer can do the same to its own prompt.
4. **Coarse fingerprints.** A file already dirty before the call is fingerprinted by its +/- line counts, and an untracked one by the word `untracked`. So the check misses an agent's rewrite of the engineer's uncommitted `justfile` edit with the same line counts, or of an untracked prompt or skill file `sssf-update` hasn't committed yet.
5. **Rollback from the index.** `_roll_back` restores a path with `git checkout -- <path>` from the index. A file the agent removed from the index with `git rm` stays deleted. The run stops and names it, but the file isn't restored.

The fixed list leaves out files that reach the factory too.

6. **`local.just`.** The starter `justfile` has `import? 'local.just'`, so a `set shell` line there runs every recipe, `just merge` included, through the agent's shell. Confirmed with just 1.59.
7. **Modules beside the ADW scripts.** `uv run adws/adw_x.py` puts `adws/` first on `sys.path`. A builder that adds `adws/yaml.py` (or a package `adws/yaml/`) gets it run in place of PyYAML on the next run, before any guard loads. `adws/adw_*.py` doesn't match it.
8. **The rest of `.claude/`, `.pi/` and `.mcp.json`.** Other skill folders under `.claude/skills/`, `.claude/agents/` (in the hub, the bypass reviewer's brief) and `.claude/hooks/` are open. So are pi's project files under `.pi/` (extensions, `settings.json`) and `.mcp.json`.

And two config cases are wrong:

9. **A `data_dir` like `./adws/adw_data`** never matches a git path, so the factory entries under it protect nothing.
10. **The trace db grant.** `observability.db` set to `adws/adw_data/` or `adws/adw_data/**` makes the db grant re-grant all of `data_dir`, prompts included. A db path given as `./adws/adw_data/sssf.db` gets no grant.

## What should change

1. **Snapshot paths exactly.** Read the diff and the untracked list with `--no-renames` and `-z`, so a rename shows as a delete and an add at their real paths, and a name is never quoted.
2. **Index flags can't hide a change.** A path with the skip-worktree or assume-unchanged flag set appears in the snapshot by its content, flagged or not. Use `git ls-files -v -z`, or another way the planner finds; any path, not only fixed ones.
3. **Fingerprint by content.** A changed or untracked path's fingerprint includes its content hash (`git hash-object`, or the blob id), so any rewrite of an already-dirty or untracked file registers as a change.
4. **Roll back from `HEAD`** for a path that was clean before the call, so a file the agent removed from the index comes back. A path that was dirty before the call is left as today, and the message says so.
5. **Widen the fixed list.**
   - `local.just`.
   - `adws/` as a whole, in place of `adws/adw_modules/`, `adws/adw_sssf_config/` and `adws/adw_*.py`. The runtime under `data_dir` stays writable, since `always_writable` is checked first.
   - `.claude/` as a whole, in place of `.claude/skills/sssf/`, `.claude/settings.json` and `.claude/settings.local.json`.
   - `.pi/` and `.mcp.json`.

   As before, an agent whose own `writes` names one of these paths may change it.
6. **Find what else the launch loads.** List every file in the repo tree that `just <recipe>` or `uv run adws/adw_*.py` reads before the guard code loads: the justfile's imports and dotenv file, `uv.toml`, `pyproject.toml`'s `[tool.uv]`, `.python-version`, and anything else. Protect what is SSSF's. Report in the spec what is the project's own (`pyproject.toml`, `.env`), and leave it unprotected.
7. **Normalise paths from config.** `data_dir` and `observability.db` are compared as clean repo-relative paths, with `./` and doubled slashes removed. The db grant applies only to a file path, never one ending in `/` or holding a glob character.
8. **Docs.** Every skill doc that names the protected paths, the always-writable grant or how the after-call check sees a change says what is true after this run. Find them with `grep -rln "protected_files\|always_writable\|adw_\*\.py" .claude/skills/sssf --include=*.md`. Today that finds `SKILL.md`, `references/config.md`, `references/handoff.md`, `cookbooks/update_modules.md`, and the builder, planner and ticket_builder prompts.
   - `references/config.md`, "Write permissions, `writes` and `protected_files`", gets the new list. It gets a line for each project file that now takes an unlock to edit: other `.claude/skills/`, `.pi/`, `.mcp.json`. And it gets a line saying the check doesn't see gitignored files.
   - The starter rosters' comments on `protected_files` say the same in a line.
   - An agent prompt that tells the agent what it may write names the new entries it would otherwise trip on. That means `adws/` and `.claude/` as wholes, not only the old entries.

## The bar

Run 37d69462's bar stands, and its spec's baseline is still `main`.

- Every refusal at `main` and at 6472edf stays a refusal.
- Each case in "The problem" above, from 1 to 10, is refused, or for 5 restored, for every starter agent of the Claude and pi rosters that doesn't name the path in its `writes`. This holds on a tree where `.git/info/exclude` lists none of the paths.
- No new refusal of a starter chain's normal work, beyond edits to the newly listed paths, which take an unlock. A quality step that writes into a fixed path's gitignored files (`__pycache__/`, `node_modules/`) is normal work, and the snapshot doesn't see it.
- The snapshot's time grows with the number of changed and untracked paths, never with the tree's size.
- No new module-level mutable state.

## Testing

In temp git repos with the real starter rosters, for both Claude and pi, through `snapshot` and `enforce`:

- each case above, from 1 to 10, as a regression test that fails on 6472edf and passes after;
- an unlocking `writes` entry still allows each new list entry;
- a builder's normal edit to product code, the planner's spec and the documenter's `docs/` all still pass, alongside test runs that write `__pycache__/`.

The tests never start a real agent.

## This run is exposed to the gap it fixes

The hub runs `main`'s `adws/`, which has every hole above. The change belongs in `.claude/skills/sssf/`, never in `adws/`. After the run, the engineer's session checks `git diff main..HEAD -- adws/` and `git status --short adws/`, expects both empty, and runs the `bypass-reviewer` subagent on `main..HEAD`.

The hub stays on `local_guard: agentsh`. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

## Out of scope

- **Gitignored files in fixed paths.** These are `.claude/settings.local.json` where `.git/info/exclude` lists it (as in the hub), a `.pyc` under `adws/adw_modules/__pycache__/`, and the skill's `node_modules/`. Python and node write these during normal work, so a check after each agent call can't tell the agent's writes from theirs. An agentsh write rule on the fixed paths is the likely fix, and it goes to `docs/open.md` for its own design.
- An agent gitignoring its own new file in a fixed folder, with a permitted `.gitignore` edit. This is the same class of hole.
- Any agent writing another run's worktree under `<data_dir>/worktrees/`.
- `CLAUDE.md`, `AGENTS.md` and other context files, which the documenter edits as normal work.
- Telling the engineer's own edits during a call apart from the agent's.
- agentsh policy changes, installing SSSF outside the tree (ADR 0001), and the export design.
