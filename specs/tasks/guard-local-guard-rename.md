# Rename `local_guard: text` to `local_guard: none`

On 2026-11-15 the engineer found the name `text` confusing. `local_guard` picks the optional layer around SSSF's base guards on the host, and `text` means no layer. But `text` reads as if it switched on the base guards' text checks, which run in every mode under both values. `docs/concepts.md` now explains the two layers (082cda0). This change fixes the name itself. Run it with `--worktree` and `--base main`, on the hub's own config.

`repo_settings.py`, the module that reads the setting, isn't on CLAUDE.md's guard list. The change still goes through this chain, because `local_guard` decides whether agentsh wraps an agent, and `agentsh_guard.py`, `agent_cc.py` and `agent_pi.py` read its value.

## The changes

1. **`none` is the new name, and `text` stays as an alias.** `RepoSettings.local_guard` accepts `none`, `text` and `agentsh`, and normalizes `text` to `none` at load. Every later read sees `none` or `agentsh`, never `text`. `LocalGuard.mode` becomes `Literal["none", "agentsh"]`. The default is `none`. Pick `none` because it matches `sandbox: none`. Not `off`: YAML 1.1 reads `off` as false.
2. **Values match as they do today.** The match is case-sensitive. A missing key gives the default. `null`, an empty value or any other string refuses, as an unknown value refuses today. `local_guard: agentsh` with a VM sandbox still refuses.
3. **Every comparison uses the new value.** The `== "text"` tests in `agentsh_guard.read` and elsewhere, and `LocalGuard(mode="text")` in the skill's tests, use `none`.
4. **What SSSF says uses the new name.** These say `local_guard: none`:
   - each refusal and hint that tells the engineer to "set `local_guard: text`", in `agentsh_guard.py`;
   - the comments in `repo_settings.py`, `data_types.py`, `agent_cc.py` and `agent_pi.py`;
   - `templates/repo.yaml`'s value and its comment;
   - `templates/justfile`'s agentsh recipe comment.
   
   The `repo.yaml` comment says in one line that `text` is the old name and still works.
5. **Docs.**
   - The current-state docs say `none`: `docs/concepts.md`, `docs/guide/install.md`, `docs/guide/run.md`, `references/config.md`'s settings table and agentsh section, `GLOSSARY.md`'s local guard entry, and the label in `images/gen.py` with `images/10_run_modes.svg` regenerated from it. `gen.py` lags the committed SVG: its guard rows say "text checks" where the SVG says "base guards" and "base guards + agentsh". Bring `gen.py` up to the SVG's labels first, so the regenerated SVG changes only the `local_guard` label.
   - `concepts.md` keeps one sentence on the old name, saying `text` still works.
   - History stays as it was: `docs/designs/`, `docs/projects.md`, `docs/open.md`, `specs/` and `app_docs/` name the value a run or repo had at the time.

## The bar

- **The two names behave alike.** A test loads a `repo.yaml` with `local_guard: text`, then one with `local_guard: none`, and one with no key. All three give the same `RepoSettings` and the same `LocalGuard`. Each launches a Claude Code agent and a pi agent with the same argv and environment, with no agentsh wrap.
- **agentsh is unchanged.** Under `local_guard: agentsh`, the rendered policies, the launch argv and environment, and every check before launch are byte-identical to `main`'s.
- **Fails closed.** These refuse at load, with a message naming the allowed values:
  - `None`, `TEXT`, `Agentsh`;
  - `off`, `false`;
  - `null` and an empty value;
  - a list.
  
  No value reaches `LocalGuard` except `none` or `agentsh`. A request with no `LocalGuard` still refuses on a local tree.
- **No stale value.** A test greps the skill's Python under `templates/adws/` and finds no `"text"` compared against a guard mode.
- **An installed repo keeps working.** The hub's own `repo.yaml` and the projects' copies keep their values, and `text` keeps loading. `install.py --update` lists `repo.yaml` as customized wherever a repo changed it, and replaces it only where the repo never did, as for any template.

## Running the tests under agentsh

agentsh refuses every `git push`, a push to a local path included, and three refusals stop the call. `tests/test_export.py` and `tests/test_install_hook.py` push to bare repos under the test's temp folder, so the builder leaves them out of its own runs: `pytest tests -q --ignore=tests/test_export.py --ignore=tests/test_install_hook.py`. Neither file touches what this change does. The chain's verify phase runs as code, outside agentsh, and runs both.

The same holds for the bypass reviewer and any harness an agent writes. To give a bare repo a commit, fetch into it instead: `git -C <bare> fetch -q <work> main:main`.

## Out of scope

- The hub's own install under `adws/`, which `just hub-update` brings in after the merge.
- The projects' `repo.yaml` files. Each project picks up the template at its next update.
- Printing a run's guard layers at its start, a separate change.
- Any change to what `agentsh` does.
