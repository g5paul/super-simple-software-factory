# Find the main checkout without trusting git's location variables

Run it with `--worktree`, from a branch cut from `sssf/9f9522bd`'s tip, with `--base sssf/9f9522bd`. That run added `host_agents` to `repo.yaml`, and this one closes a bypass the second review found in it. `worktree.py`, `budget.py` and `session.py` decide which `repo.yaml` SSSF obeys, so they're guard code, and the change goes through the chain.

## What broke

`host_agents: false` refuses a launch whose agents would run on the host. It reads the setting from the main checkout's `repo.yaml`, found by `worktree.main_checkout()` (and `budget._root()`), so that a run's worktree can't change it. A VM agent can still turn the setting off:

1. The agent commits two things to its run's branch:
   - a `.env` holding the line `GIT_DIR=sub/gd`;
   - a bare git repo in `sub/gd`, committed as plain files.

   Neither path is protected, and `permissions.judge_commits` passes both. `sub/` has no `repo.yaml`.
2. A chain process starts with the run's worktree as its working folder. `adw_modules/utils.py` calls `load_dotenv()` at import, and `find_dotenv()` finds the worktree's `.env` first, so `GIT_DIR` lands in `os.environ`.
3. `main_checkout()` runs `git rev-parse --path-format=absolute --git-common-dir`, which honors `GIT_DIR`. It returns `<worktree>/sub`, where `repo.yaml` is missing, so `host_agents` falls back to `true`. The run's record is missing from that folder's database too, so a VM run reads as a host run, and the agent runs on the host.

No one has to type anything for this to fire. On a no-flag resume of a Vercel or E2B run whose SDK isn't installed, `sandbox_remote.require_sdk` re-execs the chain with the worktree as the working folder. That fresh process reads the agent's `.env`. Starting any chain from inside a run's worktree fires it on any provider.

The hub session reproduced the redirect in a throwaway repo. Without the `.env`, the lookup found the real checkout. With it, the lookup landed on `wt/sub`, which has no `repo.yaml`.

The same redirect works on `main` for every setting SSSF reads from the main checkout:
- the spend caps;
- the sandbox default;
- the trace database a resume consults.

Run 9f9522bd's test 8 ("an agent can't turn it off") didn't catch it. The test drives the chains in-process, after `load_dotenv()` has already run against the main checkout's `.env`.

The first run's own review also listed three holes that `config.md` doesn't name:
- launching from a nested repo or submodule inside the checkout, whose own `repo.yaml` is missing;
- `just rebase`, which runs `worktree_setup` and the repo's checks on the host, on the agent's rebased code;
- an engineer-written ADW that calls `session.ensure(..., agentless=True)` and then starts an agent.

## The change

1. **`main_checkout()` and `budget._root()` ignore git's location variables.**
   - Whatever `.env` or the shell sets, the git call that finds the main checkout runs without `GIT_DIR`, `GIT_COMMON_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE` and `GIT_CEILING_DIRECTORIES`. Keep `GIT_DISCOVERY_ACROSS_FILESYSTEM` out as well.
   - The spec lists every git variable that changes which repo `rev-parse` finds, from git's own docs (`git help git`, ENVIRONMENT), and strips each one. It also finds every other place SSSF works out the main checkout or the repo root, and shows each one either goes through the fixed lookup or can't be moved by a worktree's `.env`.
   - Strip them for that lookup only. `os.environ` stays as it is: SSSF must not change what the repo's own tools see, beyond what it already changes.
2. **Say where the setting comes from.** The spec says whether `load_dotenv()` should also stop loading `GIT_*` names from `.env`. It also says whether a worktree's `.env` should be read at all when a chain starts inside a worktree. Prefer the narrowest fix that closes the redirect: the lookup fix alone if it does.
3. **Docs.** `config.md`'s `host_agents` text names the three holes above, beside the `just quality` one:
   - a nested repo or submodule;
   - `just rebase`;
   - an ADW that claims to start no agent and then starts one.

   Each gets one sentence on what to do. Add one `docs/open.md` row covering `rebase` and `quality` running agent-written code on the host.

## The bar

- **The bypass refuses.** A test builds a main checkout with `sandbox: vercel` and `host_agents: false`, then a run worktree whose branch commits the `.env` and the `sub/gd` bare repo. In a fresh Python process, the way `uv run adws/<chain>.py` starts, with the worktree as its working folder, the launch refuses with the `host_agents` message. The same test with `GIT_DIR` exported in the shell refuses too. Each test starts a subprocess, never an in-process import, so `load_dotenv()` really runs.
- **The fake fails like the real one.** Without the fix, the fresh-process test lets the launch through, so it can't pass by accident.
- **The resume path.** A no-flag resume of a VM run whose worktree holds the planted `.env` still finds the run's record and stays a VM run, through the `require_sdk` re-exec or a test that starts the process the same way.
- **Every setting.** `budget._root()` from inside that worktree returns the real main checkout, so the spend caps and the sandbox default come from the real `repo.yaml`.
- **Nothing else changes.** Every existing test passes. A plain run, a worktree run and a VM run find the same main checkout as before. The repo's own commands see the same environment as before.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. The fresh-process tests start Python subprocesses with fake agents and no real VM. Keep runs light, since the agentsh event store slows under heavy load (`docs/open.md`).

## Out of scope

- Refusing or sandboxing `just quality` and `just rebase`. Document them and add the open.md row.
- Refusing an agent's commit of `.env` or a git repo under another name. Add an open.md row if the spec finds it worth one.
- The P2 row on committed secret files, and the other open rows.
