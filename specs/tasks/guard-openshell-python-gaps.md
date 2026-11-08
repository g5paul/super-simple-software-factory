# Close the two gaps in the OpenShell image's Python

Guard run 609ad1c5's review found two broken promises in how `--sandbox openshell` picks the image's Python, and `docs/open.md` lists them. Both come from `sandbox_remote._host_python`, which `sandbox_openshell.image_python` calls for the image and `Remote.open` calls for the run's `UV_PYTHON`.

1. **The caller's environment picks the Python.** `_host_python` runs `uv python find`, which prefers the active `VIRTUAL_ENV`. Under `uv run`, that's the calling script's own cached environment. `scripts/openshell_image.py` and the `adws/` scripts each have their own, so in a repo with no `.python-version` they can find different Pythons and compute different image tags. Then `require_sdk` refuses a run whose image was just built. The review showed tags `97a1767839de` and `084d8cc9522e` for one checkout run under `uv run --python 3.12` and `--python 3.13`.
2. **Unchecked output gives a traceback.** `_host_python` returns the interpreter's stdout as is. An interpreter that prints anything else, such as `3.13\nhello from sitecustomize`, reaches `containerfile`'s check and raises a `ValueError` traceback in `require_sdk`, `_create` and `scripts/openshell_image.py`, not a plain refusal.

## What should change

- **The Python doesn't depend on the caller.** `_host_python` runs `uv python find` without the calling process's `VIRTUAL_ENV`, so the setup script, a run and a check in the same checkout get the same answer. Keep `UV_PYTHON` if the engineer set it on the host to a version: that's their choice, not the caller's environment. Run the interpreter with `-I -S`, so no `sitecustomize` or `.pth` file runs or prints.
- **`_host_python` returns a version or `""`.** Its answer is a major.minor matching `^\d+\.\d+$`, or `""`, as its docstring promises. Anything else is `""`, with the interpreter's path and its output, cut short, kept for the caller to report.
- **OpenShell refuses plainly.** Where `image_python` has no version because the interpreter's output didn't parse, `require_sdk`, `_create` and `scripts/openshell_image.py` stop with one plain message naming the interpreter and what it printed: `SandboxFailed` in the run, exit 2 from the script. No traceback. A host with no uv or no Python keeps today's behaviour, an image with no Python of its own.
- **Vercel and E2B** get the same `_host_python`. For them, a bad answer means no `UV_PYTHON`, as a host with no Python does today.
- **Docs.** Delete the row in `docs/open.md` for the two gaps. The design's "As built" line on the image's Python says the version comes without the caller's `VIRTUAL_ENV`. `references/config.md` says so in the OpenShell section if it names how the version is picked.

## Settled by guard run 6ff4f245's spec review

Run 6ff4f245 stopped after three spec reviews, each finding a real route in the draft before. Its last draft, `adws/adw_data/sessions/6ff4f245/drafts/6ff4f245_openshell-python-gaps_v4.md`, with the reviews beside it in `../context_handoff/`, has the design they settled on; start from it.

- **`uv python find --system`.** Dropping `VIRTUAL_ENV` isn't enough, since `uv run` also puts its `bin` first on `PATH`. `--system` skips both, and a committed `.venv` no longer runs.
- **`UV_PYTHON` counts only as a version.** `utils.py` loads `.env`, which an agent can edit, so a path there would run on the host. Pass the variable to uv only when it fully matches `\d(\.\d{1,2}){0,2}` with `re.ASCII`. uv reads `3.999`, `256.1` or `99999999999` as a program name and runs a file of that name found on `PATH`; the narrower pattern admits none of those. Anything else is ignored, as the baseline ignores every form.
- **Tests.** An autouse fixture in `conftest.py` clears `UV_PYTHON`, since every VM command carries it. The script's tag-equality test runs the script directly for its second reading.

## The bar and its one exception

The change must not run agent code where the baseline wouldn't, with one exception the engineer accepted on 2026-11-08. Without the caller's environment, uv's search goes on along `PATH`. On a host with no uv-managed Python that meets the request, called from a `uv run` environment that stopped the baseline's search, a `python` or `python3` an agent planted in a folder on `PATH` that it can write now runs. A local agent that can write such a folder can already plant a program the engineer's next command runs, so this adds a route, not a power. State it in the spec's bar and known limits with those conditions, add a real-uv test that shows it with a control, and judge findings against it. Any other file that runs where the baseline's wouldn't is a bypass.

## Tests

- With `VIRTUAL_ENV` set to a fake environment whose Python differs, `_host_python` gives the same answer as without it, using stand-in `uv` and interpreters on `PATH`.
- `UV_PYTHON` set on the host still decides it.
- An interpreter that prints an extra line gives `""` from `_host_python`, and a plain refusal from `require_sdk`, `_create` and the script, never a traceback.
- The interpreter runs with `-I -S`.
- `UV_PYTHON` of `./evil/python`, `3.999` or `256.1`, with a planted file of that name in the cwd and on `PATH`, runs nothing, using real uv and a control that shows the file would run.
- Every existing test passes unchanged.

## Out of scope

- The two older holes in `docs/open.md`'s next row: the worktree against the main checkout, and a committed `.venv/bin/python` the host runs.
- The network policy.
- Building an image or running a sandbox.
