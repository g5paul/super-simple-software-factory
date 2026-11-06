# Open items

This is the hub's one list of open SSSF work. Each design keeps its reasons and decisions; the to-do lives here, with a link back. None of these blocks a run.

To add an item, add a row and link the doc that explains it. When an item closes, delete its row: the commit says what closed it, and the design doc records the outcome where it matters. Items about an unlisted project are kept in the private list, never here.

| Kind | Means |
|---|---|
| build | known work, not built yet |
| watch | waits on something a real run will show |
| need | waits until a repo needs it |
| decide | waits on the engineer |

## SSSF

| Item | Kind | Since | Background |
|---|---|---|---|
| Agents can read `.env`. A `forbid` rule refuses shell commands that touch it, but an agent's Read tool or a `grep -r` over the repo still reads it, since SSSF bounds what agents write, not what they read. Close this before a repo that uses such a rule keeps a real secret in `.env` | build | 2026-11-03 | [config.md](../.claude/skills/sssf/references/config.md#rules-for-every-agent--agent_rules), which says a rule reads shell commands only |
| `worktree_setup` runs every command for every run, even one that touches a single package. Running less needs the run's scope before its first agent, and only the planner knows it, later. It costs about a second today. Reopen if a repo's setup grows slow; the simplest fix is the engineer naming the packages at launch | build | 2026-10-30 | [parallel-worktree-runs.md](designs/parallel-worktree-runs.md#decisions), decision 16 |
| A narrower VM network: a domain allowlist, and the provider's proxy adding the token instead of SSSF passing it in. Matters only for untrusted code | build | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md) |
| Rebase checks off the host. Today `just rebase` runs its setup and checks on the host | build | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#decisions), decision 11 |
| A trial judge that picks the winner on its own, once enough scorecards show whether its picks match the engineer's | need | 2026-11-01 | [trials.md](designs/trials.md) |
| A second sandbox kind. Nothing asks for one yet | need | 2026-10-31 | [container-operator.md](designs/container-operator.md#what-a-second-sandbox-means) |
| Whether a real ChatGPT 429 reads the way pi's source says. The first one gets a log row | watch | 2026-11-01 | [pi-usage-stop.md](designs/pi-usage-stop.md) |
| Whether `/tmp` lasts across a Vercel VM refresh. The obs relay reinstalls its script on each call, so a loss should cost nothing; no proof run has lasted long enough to refresh | watch | 2026-11-05 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-the-obs-relay), decisions 37 to 50 |
| Which exception Vercel's SDK raises when a session ends mid-stream. Nothing to build until the trace shows one | watch | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-vercel) |
| How far a day of real runs is from Vercel's Hobby limits past the allowance: 40 vCPUs a minute on resume, and the snapshot storage cap | watch | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-vercel) |

## Projects

Open items for a listed project, for the engineer to settle with that repo's session.

| Item | Kind | Since | Repo |
|---|---|---|---|
| None | | | |
