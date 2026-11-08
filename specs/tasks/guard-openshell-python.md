# Ship the repo's Python in the OpenShell image

The proof run 62d231db in sssf-sandbox ran every agent to the end under `--sandbox openshell`, then failed its checks. `sandbox_remote` gives each VM command `UV_PYTHON`, the Python the host's uv picks in the tree (`_host_python`), 3.13 there. The image has Ubuntu's Python 3.12, so `uv run pytest` tried to download 3.13 from `releases.astral.sh` and GitHub, the policy denied both, and the test check failed three times. Vercel and E2B download it; an OpenShell sandbox can't, and GitHub stays closed (design decision 3 in `docs/designs/openshell-sandbox.md`).

## What should change

- **The image holds the host's Python.** `containerfile` in `adw_modules/sandbox_openshell.py` gains the Python version, as major.minor, and installs it with uv at build time, where the build has network. Put it under `/usr`, which the policy already gives read access, and make it one the sandbox user's uv finds with no environment variable of its own, for example an executable `python3.<minor>` on `PATH` from `UV_PYTHON_BIN_DIR`. Check uv's `--help` for `uv python install` for the flags it has. The build's last `RUN` line checks that `uv python find <version>` succeeds.
- **One source for the version.** The version is `sandbox_remote._host_python` of the repo root, the same value the run passes as `UV_PYTHON`. `image_tag` hashes the Containerfile, so the version is in the tag. `require_sdk`, `_create` and `scripts/openshell_image.py` all get the tag the same way, so a run whose Python changed since the image was built fails in `require_sdk` with the message that names `just openshell-setup`, as a missing image does today. With no host Python (`""`), the image installs none and the tag is as it would be without the version.
- **Docs.** `references/config.md`'s OpenShell section says the image holds the repo's Python and is rebuilt by `just openshell-setup` when it changes. The design's "As built" section gets one line. `docs/open.md`'s OpenShell row says the proof's network check passed and this was its one failure.

## Tests

- `containerfile` with a version installs it and checks it with `uv python find`; with `""` it installs none.
- Two versions give two image tags.
- `require_sdk` refuses, naming the recipe, when the image for the host's current Python is missing.
- Every existing test passes unchanged.

## Out of scope

- Any change to the network policy. `releases.astral.sh` and GitHub stay closed.
- More than one Python per image.
- Building the image or running a sandbox. The engineer reruns the proof afterwards.
