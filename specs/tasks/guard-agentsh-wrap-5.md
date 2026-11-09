# Finish the real-run fixes without the symlink allow

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md`. The chain run 8f8304ae, from `specs/tasks/guard-agentsh-wrap-4.md`, stopped after round 2 because round 1's fix added a hand-kept list. Every finding came from item 6, the relative-symlink allow. Its code is already on this branch, reviewed through snapshot cee7192. **Keep it, and make only the changes below.** `specs/8f8304ae_agentsh-real-run-fixes_v2.md` is the spec to revise. `docs/designs/agentsh/guard-run-8f8304ae.md` is the run's report. The design doc's section "The chain run for the real-run fixes, 8f8304ae" records the engineer's decisions, so don't reopen them.

## What should change

1. **Drop item 6.** Remove `sssf-relative-links`, the `sssf-link-targets` rule, `LINK_FOLDERS` and `relative_secret_forms`, along with their tests. A relative symlink falls to agentsh's own `default-deny-files` again, as on `main`. An absolute link in an allowed place keeps working as on `main`, so `uv venv` does too. The engineer accepted the cost: an agent's relative `ln -s` is refused, and so is an agent's `bun add` or `npm install`, because `node_modules/.bin` links are relative. `worktree_setup` runs outside the wrap and isn't affected. Add a test that a relative symlink in the workspace is denied. In the design doc, replace the "Relative links, the refuse script and the sessions folder (built)" paragraph's link part with what's built now, and correct "The push rule, third form" to the pattern below.
2. **Widen the alias pattern.** The third `sssf-no-push` pattern becomes `(?is)^\S*\s+-.*\salias\.[^=\s]*=.*push`, as `docs/designs/agentsh/sssf-net.yaml` now holds. It's tested on the fork's engine at 3 depths against 41 cases, the five real argv lists among them. Any alias value naming `push` is denied, in any case: `git -c ALIAS.p=push p`, `git -c 'alias.p=!f(){ git push; }; f' p`, `git -c 'alias.p=!git;push' p`, `git -c "alias.p=!sh -c 'git push'" p`. `git -c alias.st=status st` and `git -c alias.lg='log --oneline' lg` run. Remove the alias-value limits from `config.md`.
3. **Known limits.** `config.md` gets:
   - relative symlinks and the package installs above,
   - the workspace root swapped for a link: an agent can replace its workspace with an absolute link to a folder it made, which `main` also allows. Host git in `run.repo_root` would follow it, and the after-call guard's swapped-directory check is expected to stop the run, though no one has run it under the service.
4. **Everything else stays.** Items 1 to 5 and 7 of the last task are done and passed review: the push rule, the refuse-script read, the `null` page, the refusal message from `argv`, the sessions folder allow, and the real-data tests.

## The bar

The earlier bars stand. An item 6 mechanism coming back in any form is a finding: an allow on relative link targets, a list of link folders, or a special case for secret names in link targets. A relative symlink denied by the engine's fallback, and the limits above, are accepted and not findings.

## Out of scope

Moving the handoff folder into the worktree, a fork patch so agentsh resolves a link's target against its folder, pi under the wrap, running a real agent under the service, sudo, and turning this on outside sssf-sandbox.
