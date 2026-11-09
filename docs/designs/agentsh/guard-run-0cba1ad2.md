# Guard change report

Outcome: round 1 found nothing that blocks

Heads reviewed: `e0a92c1f5ec1`

## Bypasses (0)


## Harms (0)


## False positives (0)


## Broken promises (0)


## Old holes (6)

- `'agentsh_sssf.yaml is empty, then: uv run adws/adw_agentsh_policy.py'` passes agentsh_guard.render_config, as adw_agentsh_policy.py calls it. Base: passes. Head: passes.  The spec left this open for a reviewer to file. render_config raised TypeError when called directly, and the policy script doesn't catch it, so the script gives a traceback. It's the engineer's own command and no agent runs. Base has no such script. New to the known limits.
- `'agentsh_sssf.yaml holds the bytes \xff\xfe\xff, then a run starts'` passes agentsh_guard.read. Base: passes. Head: passes.  The spec left this open for a reviewer to file. read catches only OSError on the template, so it raises UnicodeDecodeError before any agent starts. Base has no template. New to the known limits.
- `'git -c include.path=/tmp/x p'` passes sssf-no-push patterns, and rules.breach with the shipped push rule. Base: passes. Head: passes.  A push with the word nowhere in argv. The same holds for 'git --config-env=alias.p=VAR p' and 'GIT_CONFIG_GLOBAL=/tmp/x git p'. They belong with the GIT_CONFIG_COUNT limit, but config.md names only that one. The spec's threat model allows for this, and the network rules still refuse GitHub. New to the known limits.
- `'/usr/lib/git-core/git-push origin'` passes sssf-no-push commands list, and rules.breach with the shipped push rule. Base: passes. Head: passes.  git-push exists on this machine as a link to git, so this isn't the copied-binary limit. Judged from the rule text: commands: [git] matches the basename, per the repo_settings comment. The service wasn't run. The network rules still apply. New to the known limits.
- `"exec -a $'g\\nit' git push"` passes sssf-no-push patterns, and rules.breach with the shipped push rule. Base: passes. Head: passes.  Judged on the argv ['g\nit', 'push'], and a tab behaves the same with ['g\tit', '-c', 'a=b', 'push']. This matches the accepted argv[0] holding a space, but config.md names only a space. New to the known limits.
- `'on_attach_failure: "fail_open" in both agentsh_sssf.yaml and the installed /etc/agentsh/sssf.yaml'` passes agentsh_guard.check_machine. Base: passes. Head: passes.  The revised spec's bar says a config with enforcement off is a bypass if check_machine passes it. The check call was run; the route to it comes from reading the code. An agent edits the template and gets stopped, the stop path doesn't call enforce so the edit stays, and the next run's FIX message sends the engineer to the script and sudo, which installs it. It needs the engineer's sudo step, and the text guard stays on, so it's no worse than the baseline. Base has no check_machine. New to the known limits.

173 candidates, 524 checks.
