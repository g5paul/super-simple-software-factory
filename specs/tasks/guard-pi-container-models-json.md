# Stop a container run when pi's config gives openai a key of its own

Decision 10 of `docs/designs/container-operator.md` says a credential pi
would use ahead of the broker's token stops the run: in the container,
`agent_pi.check_stored_login` reads `auth.json` and stops on an `openai`
entry. Run 99c6f4fa's bypass review found it reads `auth.json` only. A
`models.json` in pi's agent dir, in the container's shared home volume, can
give the `openai` provider an `apiKey`. pi resolves an explicit key ahead of
a stored login and ahead of `OPENAI_API_KEY`
(`@earendil-works/pi-ai/dist/auth/resolve.js`), so that key wins over the
broker and pi bills it instead of the host's ChatGPT plan, with nothing said.
An agent in the container can write the home volume. `references/config.md`
lists this today as a known limit.

What should change: in the container (`SSSF_PI_TOKEN_SOCKET` set), the
check also reads `models.json` from the same agent dir it reads `auth.json`
from, under the same rules: a regular file only, never blocking, key names
only, never printing a value. A `models.json` that gives the `openai`
provider an API key, or any other credential pi would use ahead of
`OPENAI_API_KEY`, stops the run before its first pi agent with a message
naming the file and saying to remove that entry. A `models.json` that adds
models or settings without a key for `openai` passes. Find from pi 1.4.0's
own source every field there that can carry such a credential, including a
key given as a shell command or an environment-variable name if pi supports
those, and cover each one. If any other file pi reads from its agent dir can
do the same, cover it too or list it as a known limit.

Then take the `models.json` part out of config.md's known limit, and keep
what stays true. A host run, a VM run, and a Claude Code roster in the
container keep exactly today's behaviour.
