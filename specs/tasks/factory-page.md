Build the factory page's first release, the health board that `docs/designs/factory-page.md` describes under "What the first release is" ("Rows and colours", "How it reads") and "Done". This is piece 4 of its build order.

Where: the skill's dashboard, `.claude/skills/sssf/apps/visualizer` (server and UI), shown only when its server is started with `--factory <hub path>`; a `just factory` recipe in the hub's `local.just`; the data from the hub's `adws/check_projects.py --json` and `adws/phase_times.py --json`, which exist already.

Done means: what the design's "Done" section says: tests against seeded traces and a fake `/proc` for every colour rule, never the real registry, traces or `/proc`; `bun test` and `npx --no-install vue-tsc --noEmit` pass in the dashboard's folder; without `--factory` the dashboard behaves as it does now.

In your shell, agentsh denies a connect to `127.0.0.1`, so a test that starts the real server and calls it over a socket, as `server/listen.test.ts` does, fails there with "Permission denied". The chain's test step runs outside your shell and runs those tests. Test the factory code through the server's request handler and its functions, not over a socket, and treat `listen.test.ts` failing in your shell as expected.

Out of scope: everything in the design's "Deferred" list; changes to `check_projects.py`, `phase_times.py` or their `--json`; guard code, the agentsh policy and `quality.py`; the docs, which are piece 5.
