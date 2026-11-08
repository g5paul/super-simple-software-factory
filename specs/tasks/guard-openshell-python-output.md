# Check the host Python's answer before the OpenShell image uses it

Guard run 609ad1c5's review found two gaps in how `--sandbox openshell` picks the image's Python, and `docs/open.md` lists them. This change closes the second one only. The engineer decided on 2026-11-08 to leave the first open, after guard runs 6ff4f245 and e3308ebb showed that `uv python find --system` lets a planted file run on routes the baseline doesn't reach, among them uv's managed-Python folder.

`sandbox_remote._host_python` runs `uv python find` in the tree, then the interpreter it names, and returns that interpreter's stdout as is. An interpreter that prints anything else, such as `3.13\nhello from sitecustomize`, reaches `containerfile`'s check in `sandbox_openshell.py` and raises a `ValueError` traceback in `require_sdk`, `_create` and `scripts/openshell_image.py`, not a plain refusal.

## What should change

- **The interpreter runs isolated.** `_host_python` runs it with `-I -S`, so no `sitecustomize`, `.pth` file or `PYTHON*` variable runs or prints. The `uv python find` call stays exactly as it is: same arguments, same environment, same cwd.
- **`_host_python` returns a version or `""`.** Its answer fully matches `\d+\.\d+` with `re.ASCII`, or is `""`, as its docstring promises. Anything else is `""`, with the interpreter's path and its output, cut short, kept for the caller to report.
- **OpenShell refuses plainly.** Where the host's interpreter answered but the answer didn't parse, `require_sdk`, `_create` and `scripts/openshell_image.py` stop with one plain message naming the interpreter and what it printed: `SandboxFailed` in the run, exit 2 from the script. No traceback. A host with no uv or no Python keeps today's behaviour, an image with no Python of its own.
- **Vercel and E2B** get the same `_host_python`. For them, a bad answer means no `UV_PYTHON`, as a host with no Python does today.
- **Docs.** In `docs/open.md`, the row for the two gaps keeps only the first, and says that `--system` was tried in runs 6ff4f245 and e3308ebb and dropped because it reaches uv's managed folder and further along `PATH`, so a fix needs another design. The design's "As built" line on the image's Python says the interpreter runs with `-I -S` and an answer that isn't a version refuses.

## The bar

The change must not run anything the baseline wouldn't. It runs the same `uv python find`, and the same interpreter with more isolation, so any file that runs here and not at the baseline is a bypass.

## Tests

- An interpreter that prints an extra line gives `""` from `_host_python`, and a plain refusal from `require_sdk`, `_create` and the script, never a traceback.
- The interpreter runs with `-I -S`, and a `sitecustomize` that would print no longer reaches the answer.
- The `uv python find` call is unchanged.
- Every existing test passes unchanged.

## Out of scope

- The first gap, the caller's environment picking the Python, and any change to how `uv python find` is called or to `UV_PYTHON`.
- The two older holes in `docs/open.md`'s next row.
- The network policy, building an image, or running a sandbox.
