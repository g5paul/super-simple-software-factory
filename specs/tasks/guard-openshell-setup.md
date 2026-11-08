# Keep agents from running `just openshell-setup`

`just openshell-setup` in `templates/justfile` acts outside the repo. It writes a systemd user unit, TLS keys and a gateway config under `$HOME`, starts the gateway service, and builds a Podman image. The run cookbook (`cookbooks/run_adw.md`, section "In a VM") tells an operator to leave it to the engineer, but nothing stops an agent from running it. A project session raised this after the 0fbee01 update. A repo's `agent_rules` could forbid it, but `templates/repo.yaml` ships with none, so no repo has one.

## What should change

- **The recipe refuses inside a run.** When `SSSF_ADW_ID` is set, which `child_env` sets for every agent, `openshell-setup` prints one line to stderr and exits non-zero before it does anything else. The line says it's the engineer's to run, outside an SSSF run. That holds however the recipe is called: `just openshell-setup`, `just sssf openshell-setup` where SSSF's recipes are a module, or from a script.
- **The image script refuses too.** `scripts/openshell_image.py` refuses the same way when `SSSF_ADW_ID` is set, unless `--print` is given, which only shows the tag and the Containerfile and runs no Podman.
- **Docs.** `references/config.md`'s OpenShell section says the recipe refuses inside a run. The run cookbook's line on `just openshell-setup` says the recipe enforces it.

## The bar

This stops an agent from running the recipe by name, the way a `forbid` rule on a billing recipe does. It doesn't stop an agent that unsets `SSSF_ADW_ID`, or runs the recipe's steps by hand (`systemctl --user`, `podman build`); a local agent could already do those. Say so in the spec's known limits, and judge findings against that bar.

## Tests

- With `SSSF_ADW_ID` set, the recipe exits non-zero, writes nothing under a fake `$HOME`, and runs no `systemctl`, `podman` or `openshell` command. Put stand-ins for those on `PATH` that record being called.
- Without it, the recipe gets past the check as before.
- The image script refuses with `SSSF_ADW_ID` set, and `--print` still works with it set.
- Every existing test passes unchanged.

## Out of scope

- A built-in rule in `rules.py`, or any change to `agent_rules`, `templates/repo.yaml` or the guard modules.
- Other recipes that act outside the repo.
