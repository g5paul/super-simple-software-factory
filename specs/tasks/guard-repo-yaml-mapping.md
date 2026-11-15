# Refuse a `repo.yaml` that isn't a mapping

This is item one of open.md's P3 row "Leftovers from guard run 7bad1e2b's reviews". Run it with `--worktree` and `--base main`, on the hub's own config.

## The problem

`repo_settings.load` (`templates/adws/adw_modules/repo_settings.py:177`) reads `adws/adw_sssf_config/repo.yaml` with `yaml.safe_load` and passes `data or {}` to `RepoSettings.model_validate`. When the file's top-level document is falsy but isn't a mapping, it loads as all defaults with no error. That covers `[]`, `false`, `0` and `''`. So a repo set to `local_guard: agentsh` drops to no layer, and every other setting resets too: `sandbox`, `secret_files`, `deny_exec`, `network_allow` and the cost caps. The model refuses a misspelled key so that it "fails, never silently defaults", and a whole file gets less care than one key. A truthy non-mapping, such as `[local_guard: agentsh]` or `agentsh`, already refuses, because pydantic rejects it.

The 7bad1e2b bypass reviewer found this on 2026-11-15 and rated it Low. An agent that can write `repo.yaml` can already write `local_guard: none`. But `repo.yaml` is protected, and a file that loses every setting without a word is a trap for the engineer too.

## The change

- `load` keeps two cases as they are. A missing file gives the defaults, and so does a file whose document is empty: no content, only comments, or `null`/`~`, all of which `safe_load` returns as `None`.
- A document that is a mapping is validated as today.
- Anything else refuses at load, with a message naming the file and saying its top level must be a mapping of settings. That covers a list, a string, a number, a boolean, and also an empty string `''`. The refusal reaches the engineer the way a `repo.yaml` validation error does today.
- Check the other loaders of engineer config for the same `or {}` pattern. `agents.py:50` reads `sssf.config.yaml` that way. If a non-mapping there loads as something that runs, fix it the same way. If it already refuses for another reason, say so in the spec and leave it. `budget.py:231` and `scripts/agentsh_install.py:134` read SSSF's own files, not the engineer's, so they're out of scope unless the planner finds a way an agent can write them.

## The bar

- **Fails closed.** Each of these refuses at load: top-level `[]`, `false`, `0`, `''`, `"agentsh"`, `[local_guard: agentsh]`, `1`, `true`.
- **Nothing else changes.** A missing file, an empty file, a comments-only file and an explicit `null` give the same `RepoSettings` as on `main`. Every `repo.yaml` `main` accepts loads to the same settings. In particular the hub's own, the template, and the 62 bodies in the 7bad1e2b reviewer's harness that loaded on base.
- **The message is useful.** It names the path and says what the top level must be.
- **Tests.** One test pins each refused case and each kept case.

## Out of scope

- What `repo.yaml` may contain inside the mapping, and the `local_guard` values.
- Who may write `repo.yaml`; it stays protected as it is.
- The other items in the 7bad1e2b leftovers row.
