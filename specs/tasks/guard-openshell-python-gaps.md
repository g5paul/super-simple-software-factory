# Close the two gaps in the OpenShell image's Python

Guard run 609ad1c5's review found two broken promises in how `--sandbox openshell` picks the image's Python, and `docs/open.md` lists them. Both come from `sandbox_remote._host_python`, which `sandbox_openshell.image_python` calls for the image and `Remote.open` calls for the run's `UV_PYTHON`.

1. **The caller's environment picks the Python.** `_host_python` runs `uv python find`, which prefers the active `VIRTUAL_ENV`. Under `uv run`, that's the calling script's own cached environment. `scripts/openshell_image.py` and the `adws/` scripts each have their own, so in a repo with no `.python-version` they can find different Pythons and compute different image tags. Then `require_sdk` refuses a run whose image was just built. The review showed tags `97a1767839de` and `084d8cc9522e` for one checkout run under `uv run --python 3.12` and `--python 3.13`.
2. **Unchecked output gives a traceback.** `_host_python` returns the interpreter's stdout as is. An interpreter that prints anything else, such as `3.13\nhello from sitecustomize`, reaches `containerfile`'s check and raises a `ValueError` traceback in `require_sdk`, `_create` and `scripts/openshell_image.py`, not a plain refusal.

## What should change

- **The Python doesn't depend on the caller.** `_host_python` runs `uv python find` without the calling process's `VIRTUAL_ENV`, so the setup script, a run and a check in the same checkout get the same answer. Keep `UV_PYTHON` if the engineer set it on the host: that's their choice, not the caller's environment. Run the interpreter with `-I -S`, so no `sitecustomize` or `.pth` file runs or prints.
- **`_host_python` returns a version or `""`.** Its answer is a major.minor matching `^\d+\.\d+$`, or `""`, as its docstring promises. Anything else is `""`, with the interpreter's path and its output, cut short, kept for the caller to report.
- **OpenShell refuses plainly.** Where `image_python` has no version because the interpreter's output didn't parse, `require_sdk`, `_create` and `scripts/openshell_image.py` stop with one plain message naming the interpreter and what it printed: `SandboxFailed` in the run, exit 2 from the script. No traceback. A host with no uv or no Python keeps today's behaviour, an image with no Python of its own.
- **Vercel and E2B** get the same `_host_python`. For them, a bad answer means no `UV_PYTHON`, as a host with no Python does today.
- **Docs.** Delete the row in `docs/open.md` for the two gaps. The design's "As built" line on the image's Python says the version comes without the caller's `VIRTUAL_ENV`. `references/config.md` says so in the OpenShell section if it names how the version is picked.

## Tests

- With `VIRTUAL_ENV` set to a fake environment whose Python differs, `_host_python` gives the same answer as without it, using stand-in `uv` and interpreters on `PATH`.
- `UV_PYTHON` set on the host still decides it.
- An interpreter that prints an extra line gives `""` from `_host_python`, and a plain refusal from `require_sdk`, `_create` and the script, never a traceback.
- The interpreter runs with `-I -S`.
- Every existing test passes unchanged.

## Out of scope

- The two older holes in `docs/open.md`'s next row: the worktree against the main checkout, and a committed `.venv/bin/python` the host runs.
- The network policy.
- Building an image or running a sandbox.
