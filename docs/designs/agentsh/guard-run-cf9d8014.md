# Guard change report

Outcome: the reviewer found that round 1's fix adds a mechanism

Heads reviewed: `4f9a348432f7`, `940f81b4c20c`

The reviewer flagged a new mechanism: State: _finished, a threading.Event with a 30 s wait in _end_session, and failure now set from the final read. New checks and code: _write in the install script, with its link refusal and temp-file replace, and a policies.dir check in check_machine. Broken promises on the SIGTERM delay and the replaced three-denial stop come from these.

## Bypasses (0)


## Harms (1)

- `'mv adws/adw_data .hidden_data; ln -s <dir> adws/adw_data  # by a builder with writes: None; the engineer then runs uv run adws/adw_agentsh_policy.py'` passes permissions.enforce, then adw_agentsh_policy.main, in a throwaway repo. Base: no such script. Head: enforce allows the swap and the link stays; the script replaces <dir>/agentsh/sssf.yaml with the server config. if path.is_symlink() or path.parent.is_symlink(): Narrower than round 1: the victim must already be at <dir>/agentsh/sssf.yaml or <dir>/agentsh/sssf-<repo>-<harness>.yaml, such as SSSF's own docs/designs/agentsh/sssf.yaml. With writes: [] or ['src/**'], enforce rolls the link back and the file is safe. Not run on a live chain, where moving the data dir may disturb the run's own trace.

## False positives (3)

- `'git commit -m "fix the push path"'` passes agentsh_guard.policy_text sssf-no-push under local_guard: agentsh. Base: allowed by breach, refusal and the pi handler; no policy exists. Head: text checks allow it; the policy rule denies it, and each denial counts toward the three-refusal stop. args_patterns: ['(^|\\s)push(\\s|$)'] Unchanged from round 1. Judged with the same regex in Python over joined arguments, after reading strings.Join and MatchString in the fork; no real exec was denied. The spec asks for this rule verbatim, so the engineer decides.
- `'git stash push -m wip'` passes agentsh_guard.policy_text sssf-no-push under local_guard: agentsh. Base: allowed by breach, refusal and the pi handler; no policy exists. Head: text checks allow it; the policy rule denies it and counts it. args_patterns: ['(^|\\s)push(\\s|$)'] Unchanged from round 1. Same evidence and same engineer decision as the commit message case.
- `'git log --grep push'` passes agentsh_guard.policy_text sssf-no-push under local_guard: agentsh. Base: not run through the text checks; config.md says the starter pattern allows it. Head: the policy rule denies it and counts it. args_patterns: ['(^|\\s)push(\\s|$)'] Unchanged from round 1. The text verdict comes from config.md, not from a run.

## Broken promises (10)

- `'three named exec denials counted live, then the final read answers 500'` passes agentsh_guard.check_stopped. Base: not applicable. Head: AgentshRefused with tripped true and tally 3.  New in this fix. Spec: reaching three raises RuleBreach with refusals=n and keeps the call's spend. By reading the code, agent_cc and agent_pi catch only RuleBreach, so no partial is set and no rule_breach event is written.
- `'an agent with no denials exits normally, and one final read fails'` passes agentsh_guard.check_stopped, agent_cc.run, agent_pi.run. Base: not applicable. Head: AgentshRefused; the stop itself was run, and the lost spend is read from the code.  New in this fix. Stopping is what the bar asks for, but the raise comes before usage is read, so the call's cost isn't added to the run, and on Claude Code the ledger isn't written. One malformed event lands here too, with a message saying the service didn't answer.
- `'SIGTERM to the run while the main thread is in the final read inside close'` passes AgentshProcess._end_session. Base: not applicable. Head: exit after 30.0 s, 0 DELETEs.  The delay is new in this fix. _finished.set() is only in the finally of the DELETE's try, so SystemExit during the read skips it and the atexit stop_tree waits out _finished.wait(30). Spec: a SIGTERM leaves no agentsh session open. The window is one final read.
- `'SIGTERM to the run while the watcher thread is ending the session after a trip'` passes AgentshProcess.stop_tree, atexit. Base: not applicable. Head: exit at once, 0 DELETEs.  The atexit stop_tree returns at once because _stopped is set, and the daemon watcher dies. Exiting without a signal is fixed: that path now gets 1 DELETE.
- `'uv run adws/adw_agentsh_policy.py  # plain install, with the ignore entries in scripts/install.py'` passes git status, git check-ignore and git add -A -n in a throwaway repo. Base: no such script. Head: ?? adws/adw_data/agentsh/, and git add -A would stage the policy and config files.  Spec: agentsh/ under the data dir is not tracked. install.py says chains ending in a commit phase call git add -A. This corrects round 1, which said the data dir was gitignored; on a stamped repo only sessions/, sssf.db* and worktrees/ are.
- `'sandbox: {ptrace: {trace: {execve: false}}}  # also trace.file, trace.network, on_attach_failure: fail_open, network.enabled: false, require_sni_match: false'` passes agentsh_guard.check_machine. Base: not applicable. Head: all pass.  Narrowed from round 1: the fix closes policies.dir only. The merged config waits in the data dir until the sudo step, so a live agent on a run without --worktree could edit it, and agents would then not be held by the generated policy. Only check_machine passing was run; the rest of the chain is read from the code.
- `'a refuse-rule call pending when the service fails: {"type": "assistant", ... "command": "git push"}'` passes agents._event_forwarder ended(). Base: ended() raises the same breach when an agent dies with a call pending; no service to fail. Head: RuleBreach with refusals=0.  ended() raising was run; AgentshRefused reaching it through except Exception is read from agents.py. Spec: three failed polls end as a failure, not a breach. The call may in fact have run, so the baseline wording may be right.
- `'network_allow: 0x7f.0.0.1, 0x7f.1'` passes repo_settings._bare_host. Base: setting doesn't exist. Head: accepted.  Spec: reject IP literals. The fix covers only a bare hex value. Only port 443 is opened.
- `'secret_files: ["a\\b"]'` passes agentsh_guard.secret_glob against secret_files._matches. Base: text guard protects the literal name. Head: text guard unchanged; the glob is **/a\b, which I expect gobwas to read as ab.  Spec: translate as _matches reads them. The fix escapes braces only. How gobwas reads a backslash is from memory; this machine has no Go.
- `'deny_exec command: null  # also true'` passes repo_settings.DenyExec, policy_text. Base: setting doesn't exist. Head: accepted; PyYAML parses them to [None] and [True].  Spec: command is a bare name. The value reaches YAML unquoted. Go's decoding wasn't tested. true is a real command.

## Old holes (1)

- `'cat /main/.claude/api.key  # secret_files: [".claude/api.key"], on a --worktree run, in a spelling the text check misses'` passes generated policy first match; the text guard wasn't run on this string. Base: passes, by reading rules.py: the text guard searches command text only. Head: passes: the policy's first match is sssf-main-config, which allows the read.  config.md promises secret files are kept from agents anywhere in the repo or a run's worktree. A pattern with a / is rooted at ${PROJECT_ROOT}, so the main checkout's copy isn't covered. Judged with my own evaluator. New to the known limits.

265 candidates, 940 checks.
