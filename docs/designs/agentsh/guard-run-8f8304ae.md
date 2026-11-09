# Guard change report

Outcome: the reviewer found that round 1's fix adds a mechanism

Heads reviewed: `57e4ebeff32e`, `cee7192adae6`

The reviewer flagged a new mechanism: A list of exceptions: LINK_FOLDERS, plus the sessions, cc-socks and git_dir entries, is a hand-kept list of link-path denies carved out of sssf-relative-links. It must track every write-allow entry, and it already misses ${PROJECT_ROOT}. relative_secret_forms also gained a special case for wildcard-only last segments, which misses ? and [...].

## Bypasses (1)

- `'mv /w/wt /m/proj/adws/adw_data/sessions/x/wt && ln -s other-repo/notes.txt /w/wt'` passes agentsh file_rules (claude and pi): symlink on the link path and the raw target. Base: stopped. Head: passes. + LINK_FOLDERS + [sessions] sssf-workspace allows ${PROJECT_ROOT} exactly, with ALL ops. A relative target at the root resolves in its parent: other runs' worktrees, or the engineer's other checkouts, which are default-denied. permissions.py runs git with cwd=run.repo_root, so it follows the link. The after-call guard's swapped-directory check probably stops the run there (not verified).

## Harms (0)


## False positives (1)

- `'ln -s /w/wt/package.json /w/wt/l'` passes agentsh file_rules with secret_files ['config/*.json']. Base: passes. Head: stopped. forms = [rel, f"**/{rel}"] if not last.strip("*") else [last, f"**/{last}"] A last segment that can match any name still gives bare forms that match absolute targets. config/*.json gives **.json. ./secrets/?* gives ?** and **/?**, and ./keys/[a-z]* gives [a-z]**; both deny nearly every symlink, including uv venv links. This follows the spec's literal 'last and **/last' rule.

## Broken promises (0)


## Old holes (1)

- `'mv /w/wt /tmp/wt && mkdir /tmp/evil && ln -s /tmp/evil /w/wt'` passes agentsh file_rules: symlink. Base: passes. Head: passes.  The workspace root swapped for a link to an agent-made folder. Host git in run.repo_root would then use that folder's .git/hooks. The after-call guard probably notices the swap. Denying symlink on ${PROJECT_ROOT} itself closes this along with the bypass. New to the known limits.

198 candidates, 4700 checks.
