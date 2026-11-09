# Guard change report

Outcome: the reviewer found that round 2's fix adds a mechanism

Heads reviewed: `b22545bdc38e`, `0185f91fe7e8`, `b625386acc1d`

The reviewer flagged a new mechanism: A new file rule, sssf-link-dotdot: a symlink deny on targets matching **/../** and **/... It's a new check rather than a narrowing of sssf-sessions, the allow that causes the harm. It brings back item 6's '..' deny, which the engineer dropped and the spec puts out of scope. It stops one target spelling but not the class, since /proc/*/fd and /dev/fd targets still work, and it causes the in-workspace '..' false positive.

## Bypasses (0)


## Harms (3)

- `'ln -s /proc/4242/fd/5 /m/proj/adws/adw_data/sessions/1234abcd/planner/envelope.json'` passes agentsh file_rules (Rules emulation, claude and pi) + agents._persist_envelope. Base: stopped. Head: passes. rule("sssf-sessions", [sessions, f"{sessions}/**"], ALL, "allow"), The target matches sssf-fd-leaves (/proc/*/fd/*, all operations). The agent finds the host's pid and descriptor through /proc/<pid>/fd, which sssf-system-read lets it list and sssf-fd-leaves lets it readlink. tracer.py keeps the trace db (<main>/adws/adw_data/sssf.db) open all run. fdlink.py shows the envelope write truncating a held-open sqlite db to 29 bytes of JSON. This assumes, as the spec says, that agentsh checks the raw target text; nobody has run it under the service.
- `'ln -s /dev/fd/5 /m/proj/adws/adw_data/sessions/1234abcd/planner/envelope.json'` passes agentsh file_rules (Rules emulation, claude and pi) + agents._persist_envelope. Base: stopped. Head: passes. rule("sssf-sessions", [sessions, f"{sessions}/**"], ALL, "allow"), The target matches sssf-devices (/dev/fd/**, all operations) and resolves in the host's process when the host writes. /dev/fd/<n>/<name> also passes, which reaches into a directory if the host holds one open.
- `'ln -s /proc/4242/fd/5 /m/proj/adws/adw_data/worktrees/1234abcd/l && mv /m/proj/adws/adw_data/worktrees/1234abcd/l /m/proj/adws/adw_data/sessions/1234abcd/planner/envelope.json'` passes agentsh file_rules (Rules emulation, rename op, claude and pi) + agents._persist_envelope. Base: stopped. Head: passes. rule("sssf-sessions", [sessions, f"{sessions}/**"], ALL, "allow"), The link is allowed in the workspace on both versions. The rename's destination is sssf-default-deny on base and sssf-sessions on head. sssf-link-dotdot doesn't apply because the target has no '..'.

## False positives (1)

- `'ln -s /m/proj/adws/adw_data/worktrees/1234abcd/web/../shared/types.ts /m/proj/adws/adw_data/worktrees/1234abcd/web/src/types.ts'` passes agentsh file_rules (Rules emulation, claude and pi, worktree and plain runs). Base: passes. Head: stopped. rule("sssf-link-dotdot", ["**/../**", "**/.."], ["symlink"], "deny"), This is what `ln -s "$PWD/../shared/types.ts" src/types.ts` makes, and it resolves inside the workspace. Spec Bar item 4 says absolute links in allowed places work as on main, and the spec keeps '..' targets out of scope 'as on main'. It's deliberate in the fix, so the engineer would have to accept the trade.

## Broken promises (0)


## Old holes (4)

- `'echo x > /m/proj/.env.example'` passes agentsh file_rules (Rules emulation). Base: passes. Head: passes.  sssf-env-examples allows every operation on **/.env.example and **/.env.sample anywhere, ahead of every deny, so an agent can write such files outside its workspace. It's also why '<sessions>/../../../.env.example' gets past sssf-link-dotdot, though that adds nothing new. New to the known limits.
- `'ln -s /proc/4242/fd/5 /m/proj/adws/adw_data/sessions/1234abcd/planner/envelope.json'` passes agentsh file_rules (plain run, Rules emulation) + agents._persist_envelope. Base: passes. Head: passes.  On a plain run sssf-workspace covers the sessions folder, so the descriptor link is allowed on both versions and the host's envelope write truncates the file behind that descriptor. Head's config.md names only a link to a workspace file. New to the known limits.
- `'git -c help.autocorrect=immediate puhs'` passes sssf-no-push args_patterns. Base: passes. Head: passes.  This stands for the round-1 push forms: quoted alias value, autocorrect and repo-local alias. config.md has listed them since round 2.
- `'mv /w/wt /tmp/elsewhere && ln -s /tmp/evil /w/wt'` passes agentsh file_rules (Rules emulation). Base: passes. Head: passes.  The workspace-root swap. It is out of scope and listed.

52 candidates, 312 checks.
