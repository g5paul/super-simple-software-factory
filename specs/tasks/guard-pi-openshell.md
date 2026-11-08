# Run pi agents under `--sandbox openshell`

`sandbox.check_roster` refuses every pi agent under `--sandbox openshell` (`sandbox.py:164-167`), as decision 5 in `docs/designs/openshell-sandbox.md` chose for v1. `docs/research/pi-under-openshell.md` found what pi needs there and nothing that blocks it; read it first, since it has the line numbers. The engineer accepted its recommendations on 2026-11-08.

## What should change

1. **Lift the refusal.** pi agents may run under `openshell`. Keep the check that every pi agent in a VM is on an `openai/` model, on every provider.
2. **pi in the image.** `containerfile` installs pi at a pinned version, one constant shared with E2B's template script so both get 1.5.0, plus `ripgrep` and `fd-find`, since pi's grep and find tools otherwise download them from GitHub, which the policy closes. The build's check line adds `pi --version`, `rg --version` and `fdfind --version`. One image serves both harnesses. The tag hashes the Containerfile, so existing images rebuild through `just openshell-setup` as for a Python change.
3. **A rule for pi's model host, only when needed.** `policy()` takes the roster's set of harnesses. A roster with a pi agent gets a rule letting `/usr/local/bin/node` reach `api.openai.com:443` with the same access the Claude rule has today. A roster with a Claude Code agent gets the existing Claude rule. A roster with only one harness gets no rule for the other host. Narrowing either rule to explicit request paths waits for the proof's audit pass.
4. **`PI_OFFLINE=1` for pi agents under OpenShell only**, so pi never tries `pi.dev` or a download, and a missing tool fails fast. Vercel and E2B keep today's behaviour.
5. **The Claude token only for Claude Code agents, on every provider.** Today `Remote.start` gives every agent `CLAUDE_CODE_OAUTH_TOKEN` (`sandbox_remote.py:616`), pi agents included. Give it only to a Claude Code agent, and read the token file only when the roster has one, so an all-pi roster runs with no `token.env`.
6. **Docs.** Decision 5 says pi runs under OpenShell from this change. The design's "As built" gains a line on how OpenShell matches a rule: against the connecting process and its parents, so a model host's rule covers every command its agent runs, and the design's line that `curl` to `api.anthropic.com` failed is corrected to say that test ran outside an agent. `references/config.md`'s OpenShell section and its pi lines say pi runs there, with the `node` rule and what it covers. Delete the `open.md` row on pi under OpenShell.

## The bar

Sort findings by decision 14. These are accepted, so name them in the spec's known limits and don't count them as bypasses:

- Any process with `node` or `claude.exe` among its ancestors, which covers every command the agent runs, can reach that roster's model host. The agent already holds a token for it, so it gains no spending power, only an upload channel to that host, which `api.anthropic.com` already gives a Claude agent.
- The pi agent holds its hour's ChatGPT access token, as on Vercel and E2B.

Anything else a VM agent can reach that it couldn't before blocks: another host, a model host's rule on a roster that doesn't need it, the Claude token in a pi agent's environment, or a token in a command line, the trace, a session file or a commit.

## Tests

- `check_roster` lets a pi `openai/` roster through under `openshell` and still refuses a pi agent on another provider, in every VM mode.
- `policy()` for a pi-only, a Claude-only and a mixed roster: each has only the rules its harnesses need.
- The Containerfile installs the pinned pi, `ripgrep` and `fd-find`, and checks all three.
- A pi agent's start frame under each provider holds `OPENAI_API_KEY` and no `CLAUDE_CODE_OAUTH_TOKEN`; a Claude Code agent's still holds the Claude token. An all-pi roster opens a VM with no `token.env`.
- `PI_OFFLINE=1` is set for a pi agent under OpenShell, and not under Vercel or E2B.
- Every existing test passes unchanged, except `test_check_roster_refuses_pi_under_openshell`, which flips.

## Out of scope

- Narrowing either model host's rule to request paths. The proof's audit pass decides that later.
- OpenShell's credential placeholders, and a longer token lifetime for long pi calls (the research's changes 8 and 9).
- Building an image or running a sandbox. The proof runs after the merge.
