# Guard change report

Outcome: the reviewer found that round 1's fix adds a mechanism

Heads reviewed: `5bf49a55890b`, `37134ffb7317`

The reviewer flagged a new mechanism: _SAFE is a list of git config overrides. It grew from 1 entry to 4 and is still incomplete: protocol.allow, per-key trailer.<name>.*, grafts. _normalised_blob is a second acceptance path, an exception that accepts files differing from HEAD by CRLF only. It tested safe: identical rendered output, and \r\r\n is still refused.

## Bypasses (0)


## Harms (1)

- `'git config core.repositoryformatversion 1; git config extensions.partialClone origin; git config remote.origin.promisor true; git config remote.origin.url ssh://example.invalid/r.git; git config core.sshCommand /path/agent.sh; rm .git/objects/<tree of HEAD:adws/adw_modules>  # left by a killed chain'` passes adw_agentsh_policy.check_sources (_changed_from_head: git ls-tree). Base: passes, no agent code run. Head: agent's sshCommand runs during the check, then refused. _SAFE = ["-c", "core.fsmonitor=", "-c", "log.showSignature=false", "-c", "core.commentChar=#", "-c", "trailer.separators=:"] Same class as round 1's gpg harm. Adding -c protocol.allow=never to the same argv stopped it in a probe. _base's merge-base and the diff --quiet call bypass _git and _SAFE entirely.

## False positives (0)


## Broken promises (4)

- `'echo <engineer commit after the run commit> > .git/info/grafts'` passes adw_agentsh_policy.check_sources. Base: passes. Head: passes.  --no-replace-objects doesn't turn off grafts in git 2.43, so the run commit is hidden. info/grafts is watched, so this needs a killed chain. Round 1's test cut at a run commit and didn't show it.
- `'git config trailer.SSSF-Run.key Not-Run  # also trailer.SSSF-Runner.key Other, trailer.sssf-run.key X; set after a run commit'` passes adw_agentsh_policy.check_sources. Base: passes. Head: passes.  %(trailers:key=SSSF-Run) maps the token to the configured key first, so the mark disappears. Needs a killed chain. Per-key -c overrides can't cover every alias; parsing %B in Python would.
- `'git config trailer.SSSF-Run.ifMissing doNothing  # in effect at commit_all'` passes git_helper.commit_all + check_sources. Base: passes. Head: passes (commit has no SSSF-Run).  The per-key setting beats the fix's global -c trailer.ifMissing=add. Needs a killed chain.
- `'git config trailer.SSSF-Run.key Not-Run  # in effect at commit_all'` passes git_helper.commit_all + check_sources. Base: passes. Head: passes (commit carries Not-Run: yes).  --trailer 'SSSF-Run: yes' is written under the alias key. Needs a killed chain.

## Old holes (0)


30 candidates, 36 checks.
