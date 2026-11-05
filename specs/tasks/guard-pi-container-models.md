# Let the roster's model check sign in through the container's broker

Decisions 8 to 16 of `docs/designs/container-operator.md` let a pi roster
run in the operator container, with each pi agent call getting a one-hour
ChatGPT token from the host's broker over `SSSF_PI_TOKEN_SOCKET`. The first
real proof in sssf-sandbox, on 2026-11-05, never reached an agent. Config
validation stopped it:

    agent 'planner': model pattern 'openai/gpt-6.1-sol' not found in pi --list-models

`agent_pi._pi_catalog` runs `pi --list-models` with the operator's
environment. In the container, pi has no stored login (decision 10 stops a
run that has one), so without `OPENAI_API_KEY` it lists no `openai` models.
Checked on the host from an empty `PI_CODING_AGENT_DIR`: no key lists none;
any `OPENAI_API_KEY` lists `openai/gpt-6.1-sol`. The broker is only asked per
agent call, after validation. The chain's tests faked pi's catalog, so they
missed it. On the host and in a VM run, the catalog is read on the host,
where pi is signed in, and works.

What should change: when `SSSF_PI_TOKEN_SOCKET` is set, `pi --list-models`
gets a token from the broker as `OPENAI_API_KEY`, the way an agent call does.
A broker error stops validation with the broker's message, so a host that
can't sign pi in shows up before any agent starts, not as a missing model.
The token is never printed or logged, and the catalog cache keeps no token.
Any other place SSSF runs pi in the container before an agent call (other
than `vm_token`, a known limit) gets the same treatment.

A test runs the model check with `SSSF_PI_TOKEN_SOCKET` set, a fake broker,
and a fake pi that lists `openai` models only when `OPENAI_API_KEY` is set,
and fails on today's code. A host run, a VM run, and a Claude Code roster in
the container keep exactly today's behaviour.
