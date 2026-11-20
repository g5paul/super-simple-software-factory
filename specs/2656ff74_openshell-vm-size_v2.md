# Give OpenShell VMs room for a real test suite

Second plan of this session. It replaces `specs/2656ff74_openshell-vm-size.md` after spec review 1: the marker file and `--was-active` are gone, and a restart is owed when `gateway.toml` is newer than the gateway's start. Everything else is as in the first plan.

## Problem

Run 00d8e1a8 (2026-11-20) was the hub's first `simple-sdlc` run under `--sandbox openshell`. Verify failed all four quality checks on the VM, not on the change: 2 CPUs, 2 GiB of RAM with no swap, `/tmp` a 1 GiB tmpfs taken from that RAM, and a 3.9 GB overlay root disk. The size is gateway-wide: the gateway starts `openshell-driver-vm` with the driver's defaults, and `sandbox create --cpu/--memory` changes nothing under the VM driver.

## The change, in short

1. `just openshell-setup` writes the three VM sizes into `~/.config/openshell/gateway.toml` and restarts the gateway only when that is safe. A new script does the file edit and the restart decision; the recipe calls it twice.
2. Every `openshell sandbox exec` SSSF makes carries `TMPDIR=/var/tmp`, and the filesystem policy lists `/var/tmp` read-write. `/tmp` stays.
3. Docs say what a VM gets and how to change it. The `docs/open.md` row this closes goes.

## What I confirmed, and how

- **The config keys.** On this host (OpenShell 0.1.7), `openshell-gateway --config <file> config preflight` exits 0 for a file with
  `[openshell.drivers.vm]` holding `vcpus = 4`, `mem_mib = 4096`, `overlay_disk_mib = 16384`, and exits 1 (`category=malformed`) for the same file with the keys spelled `memory_mib` and `overlay_disk_mb`. So the table and the three key names are right, and the gateway refuses a key it doesn't know. `openshell-driver-vm --help` lists the matching flags `--vcpus` (default 2), `--mem-mib` (2048), `--overlay-disk-mib` (4096). I could not read the gateway binary's strings or OpenShell's docs from the planning session (the binary and the network are closed to it), so preflight is the evidence. The setup script runs the same preflight before it replaces the file, so a future OpenShell that renames a key makes setup refuse, not break the gateway.
- **Not confirmed: whether the overlay disk file is sparse.** `~/.local/state/openshell/vm-driver/` is closed to the planning session. Preflight looks for `mke2fs`, `debugfs` and `e2fsck`, so the driver makes an ext4 image file, and such a file is usually made sparse, but that is a guess. The docs this change writes must say "up to 16 GiB of host disk per live VM" and must not say the file is sparse. The engineer's proof below has the one command that settles it. If it isn't sparse, three live VMs hold 48 GiB; the engineer then lowers `SSSF_OPENSHELL_OVERLAY_DISK_MIB` (8192 still fits the suite's 2.5 GB of temp several times over).
- **Not confirmed: that `/var/tmp` is on the overlay disk in the VM.** It is a standard folder of the Ubuntu 24.04 base image (mode 1777), and run 00d8e1a8 found only `/tmp` as a tmpfs. No sandbox image was visible to the planning session to check. The engineer's proof checks it with `df`.
- **Not confirmed: the exact text `systemctl show` prints for the gateway's start time.** The user bus is closed to the planning session, so I could not run it. systemd 255 is installed. Its documented default is `Fri 2026-11-20 16:13:05 UTC` under `TZ=UTC`: a weekday, a date, a time in whole seconds and the zone. The script reads only that shape and treats anything else as "can't tell", which restarts nothing and says so. The engineer's proof step 1 shows which case the real gateway gives.

## Decisions

### The sizes: 4 vCPUs, 4096 MiB, 16384 MiB

The host has 16 CPUs and 15 GiB of RAM.

- 4 vCPUs: three VMs at once use 12 of 16 CPUs and leave 4 for the host's own work (the ADW, obs, the engineer's session).
- 4096 MiB: two VMs use 8 GiB and three use 12 GiB of 15. Three is tight but fits; 6 GiB a VM would not fit three. With temp moved off the tmpfs (below), all 4 GiB is for processes, where before about 1 GiB of 2 could go to `/tmp`.
- 16384 MiB of overlay disk: the suite writes about 2.5 GB of temp, plus the clone, a venv and `node_modules`. 16 GiB leaves room for a failed run's leftovers within one VM's life.

### Overrides: three env vars at setup time

`SSSF_OPENSHELL_VCPUS`, `SSSF_OPENSHELL_MEM_MIB`, `SSSF_OPENSHELL_OVERLAY_DISK_MIB`, for example
`SSSF_OPENSHELL_MEM_MIB=6144 just openshell-setup`. An empty value counts as unset. A value that isn't a positive whole number (`[1-9][0-9]*`, ASCII digits) is refused with the variable's name, exit non-zero, and `gateway.toml` is not touched.

### A size the engineer doesn't set keeps the file's value

For each size: the override if set; else the value already in `gateway.toml` if it is a whole number of 1 or more; else the default. Reason: the gateway is machine-wide and setup is run once per repo. If an unset override meant "write the default", running setup in a second repo would undo the size the engineer chose in the first, and restart the gateway. Today no `gateway.toml` setup wrote has the table, so every existing machine gets the defaults on its next setup. To go back to a default, pass it as an override. A value in the file that isn't a positive whole number is replaced by the default, and setup says so. **Reviewer:** this is how I read "adds or updates the three sizes": add when missing, update on an override or a bad value.

### A restart is owed when `gateway.toml` is newer than the gateway's start

No state is kept. After `enable --now`, one step compares two times:

- the file's `st_mtime` (through a symlink, so `os.stat`), and
- the gateway's start: `systemctl --user show -p ActiveEnterTimestamp --value openshell-gateway`, run with `TZ=UTC` and `LC_ALL=C` in that one call's env.

If the file is newer, a restart is owed, and the step lists sandboxes and restarts only when none are listed. If not, it does nothing. This covers each case:

- **Fresh machine.** `size` writes the file, then `enable --now` starts the gateway. The start is later than the file, so nothing is owed.
- **A size changed, gateway running, no sandbox.** The file is newer: restart.
- **A size changed, a sandbox live.** The file is newer: no restart, and the engineer is told to rerun. On the rerun `size` writes nothing, so the mtime stays, the file is still newer, and the restart happens then.
- **A second run after the restart, a reboot or a manual restart.** The start is later than the file: nothing.

`size`'s unchanged path must not write the file, or every setup would look like a change.

The timestamp is in whole seconds. The rule is: owed when `floor(mtime) > start`. When the file's change and the gateway's start fall in the same second, nothing is owed. That is the right answer for the case setup itself produces, a fresh `size` followed at once by `enable --now`. The other order within one second (a gateway start, then a config write) can't come from one setup run, since `size` runs before `enable --now`.

A side effect to state in the docs: any later change to `gateway.toml`, the engineer's own edit included, makes the next setup restart the gateway when no sandbox is listed. That is correct, since the gateway reads the file only at start.

### TMPDIR: `/var/tmp`, on every `sandbox exec`

- Folder: `/var/tmp`. It exists in the image, any user can write it, and it is outside the repo's tree (the image's folder is always strictly below the home folder, `shared_folder` checks that). No image change, so no image tag changes and no repo needs a rebuild.
- Which commands: every `openshell sandbox exec` the run makes. `_VM.run_process` and `_VM.create_process` add it to the env frame, so agents, quality commands, `worktree_setup`, git and SSSF's own helper commands all get it. One place, no list of "which commands" to keep right.
- The value is fixed in the guard module. A caller's `TMPDIR` is overwritten. Nothing from the host's env, the repo or an agent picks the folder.
- SSSF's own VM paths that name `/tmp` literally (`/tmp/sssf-in.bundle`, the upload path, the `.sid` file, the obs relay's `OBS_ROOT`) don't change.

## Files

### 1. `.claude/skills/sssf/scripts/openshell_gateway.py` (new)

A PEP 723 script: `requires-python = ">=3.11"`, `dependencies = []`, stdlib only (`tomllib`, `argparse`, `calendar` or `datetime`, `os`, `re`, `subprocess`, `sys`, `tempfile`, `pathlib`). It does not import `adw_modules`. Docstring says what it does, the three env vars, and that it refuses inside a run.

Both subcommands first refuse while `SSSF_ADW_ID` is set and non-empty: one line on stderr with the same wording pattern as `scripts/openshell_image.py` ("Refusing: ... is the engineer's to run, outside an SSSF run (SSSF_ADW_ID is set)."), exit 1, nothing read or written.

Constants: `TABLE = "openshell.drivers.vm"`, `DEFAULTS = {"vcpus": 4, "mem_mib": 4096, "overlay_disk_mib": 16384}`, `OVERRIDES = {"vcpus": "SSSF_OPENSHELL_VCPUS", "mem_mib": "SSSF_OPENSHELL_MEM_MIB", "overlay_disk_mib": "SSSF_OPENSHELL_OVERLAY_DISK_MIB"}`, `UNIT = "openshell-gateway"`. The script keeps no file of its own.

#### `size --config <path>`

1. Read the overrides. Refuse a bad one as above, before reading the file.
2. Read the file as bytes and decode as UTF-8. Parse with `tomllib`. A missing file, bad UTF-8 or bad TOML: exit 1, name the file, write nothing. (The recipe creates a missing file first, as today.)
3. Work out the wanted value of each size (override, else a valid file value, else the default). A `bool` is not a valid value.
4. If all three already hold the wanted values: print one line, `gateway.toml: VM sizes unchanged (vcpus 4, mem_mib 4096, overlay_disk_mib 16384)`, write nothing, exit 0. The file's bytes and mtime stay. The restart decision depends on this.
5. Otherwise edit the text by lines (`splitlines(keepends=True)`), touching nothing else:
   - Find the header line: `^\s*\[\s*openshell\s*\.\s*drivers\s*\.\s*vm\s*\]\s*(#.*)?$`. The table's lines run to the next line whose stripped text starts with `[`, or the end.
   - A key already in the table: replace only the value, keeping the text before it, any trailing comment and the line ending. Pattern: `^(\s*KEY\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?)(\r?\n?)$`.
   - A key missing from the table: insert `KEY = N\n` directly after the header line, in the order `vcpus`, `mem_mib`, `overlay_disk_mib`.
   - No header: append at the end. Add `\n` first if the file doesn't end with one, then a blank line if it doesn't end with two, then `[openshell.drivers.vm]\n` and the three `KEY = N\n` lines.
6. Check the result before writing. Parse the new text with `tomllib`. It must hold the three wanted values as integers, and the old and new documents must be equal once the three keys, and an `openshell.drivers.vm` or `openshell.drivers` table left empty, are removed from both. If not (an inline table, dotted keys, a multi-line string that looks like a header), exit 1, write nothing, and say: the file's layout isn't one setup can edit, set the three keys under `[openshell.drivers.vm]` by hand.
7. Write the new text to a temp file in the same folder (`tempfile.mkstemp(dir=..., prefix=".gateway.toml.")`, mode copied from the original). Run `openshell-gateway config preflight --path <temp>` (timeout 30 s). Non-zero exit, `OSError` or timeout: delete the temp file, print the last lines of its stderr, exit 1. Zero: `os.replace(temp, os.path.realpath(config))`, so a symlinked `gateway.toml` keeps its link.
8. Print one line per size that changed: `gateway.toml: vcpus 2 -> 4` for a replaced value, `gateway.toml: set vcpus = 4` for an added one. Exit 0.

With the fresh file the recipe writes, the result must be exactly these bytes:

```
[openshell]
version = 2

[openshell.gateway]
bind_address = "127.0.0.1:17670"
compute_driver = "vm"

[openshell.drivers.vm]
vcpus = 4
mem_mib = 4096
overlay_disk_mib = 16384
```

#### `restart --config <path>`

It takes no other flag and reads no state but the two times.

1. Read the gateway's start: `systemctl --user show -p ActiveEnterTimestamp --value openshell-gateway` (timeout 30 s), with `TZ=UTC` and `LC_ALL=C` added to that call's env. Accept only output that matches `^\w{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)? UTC$` after stripping, and turn the captured part into epoch seconds as UTC (`calendar.timegm`), ignoring a fraction.
   - A non-zero exit, `OSError`, timeout, empty output or any other text: restart nothing, list nothing, and print on stderr: `Couldn't read when the gateway started (<what it printed, last 200 characters>), so it was not restarted. If gateway.toml changed, restart it yourself with no runs live: systemctl --user restart openshell-gateway`. Exit 0.
2. Read the file's time: `int(os.stat(config).st_mtime)`. A missing file: exit 1, name it.
3. If the file's time is not greater than the start: print nothing, exit 0. Nothing is listed, nothing is restarted.
4. Otherwise a restart is owed. Run `openshell sandbox list --names --page-size 1000` (timeout 30 s).
   - It fails (non-zero, `OSError`, timeout): restart nothing, print on stderr that the sandboxes couldn't be listed, that the gateway was not restarted and still runs on the old `gateway.toml`, and to rerun `just openshell-setup`. Exit 0.
   - It lists any name: restart nothing, and print on stderr: `gateway.toml is newer than the running gateway, but the gateway was not restarted: N sandbox(es) are live (<up to 10 names>). A restart stops every sandbox, other projects' runs included. Rerun 'just openshell-setup' once those runs end.` Exit 0. Every listed sandbox counts as live, whatever its state.
   - It lists none: run `systemctl --user restart openshell-gateway`. Non-zero: exit 1 with its stderr. Zero: print `Restarted the gateway: new sandboxes get <vcpus> vCPUs, <mem_mib> MiB of RAM and a <overlay_disk_mib> MiB disk.` (values read from the file; if a value is missing, say only that the gateway was restarted).

The script never runs `sudo`, never stops or deletes a sandbox, and runs `systemctl restart` only in that last branch.

### 2. `.claude/skills/sssf/templates/justfile` (the `openshell-setup` recipe)

Keep everything the recipe does today, in today's order, including the `printf` that writes a missing `gateway.toml`. Add two lines:

- Right after the `if [ ! -f "$conf" ]; then ... fi` block:
  `uv run .claude/skills/sssf/scripts/openshell_gateway.py size --config "$conf"`
- Right after `systemctl --user enable --now openshell-gateway`:
  `uv run .claude/skills/sssf/scripts/openshell_gateway.py restart --config "$conf"`

Update the comment above the recipe: it creates TLS keys only when missing; it creates `gateway.toml` when missing and otherwise changes only the three VM sizes under `[openshell.drivers.vm]`; it restarts the gateway only when `gateway.toml` is newer than the running gateway and no sandbox is listed; name the three env vars.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` (guard code)

- Add a constant beside `READ_ONLY_PATHS`: `TMPDIR = "/var/tmp"   # on the overlay disk; /tmp is a tmpfs taken from the VM's RAM`.
- `policy()`: `"read_write": ["/tmp", TMPDIR, "/dev/null", str(folder)]`. Nothing else in the policy changes.
- `_VM.run_process`: `data = _frame({**(env or {}), "TMPDIR": TMPDIR})`.
- `_VM.create_process`: `frame = _frame({**(env or {}), "TMPDIR": TMPDIR})`.
- Module docstring: one sentence that every exec gets `TMPDIR=/var/tmp`, which is on the VM's disk, that the policy allows it read-write, and that the VM's size comes from the gateway's config, which `just openshell-setup` writes.

Do not change `_SHELL`, `_exec_argv`, `_upload`'s `/tmp` path, the Containerfile or `image_tag`. `sandbox_remote.py` does not change.

### 4. `.claude/skills/sssf/tests/test_sandbox_openshell.py` (guard test)

- `test_the_default_policy`: the expected `read_write` becomes `["/tmp", "/var/tmp", "/dev/null", "/home/u/repo"]`. This is the one existing assertion the change must alter; every other expected value in it stays, which is the proof that the other paths and the network rules are as before.
- Add `test_every_exec_carries_tmpdir_on_the_disk(cli)`: with `osh._VM("sb")`, call `run_process` with no env, `run_process` with `env={"TMPDIR": "/elsewhere", "A": "1"}`, `run_process` with an `input`, and `create_process` (then `wait()`). For every logged `sandbox exec` call, the decoded stdin frame holds the line `TMPDIR=` + base64 of `/var/tmp` exactly once and no `/elsewhere`, and `TMPDIR` appears nowhere in `argv`. One of the calls runs `python3 -c "import os;print(os.environ['TMPDIR'])"` and its stdout is `/var/tmp`.
- Add `test_a_runs_commands_and_agents_get_tmpdir(...)`: through `Remote`, using the file's existing helpers (`_started_env` for `start`, and a box from `layout`/`_vm_box` for `run`), a `run()` command and a started agent both see `TMPDIR=/var/tmp`.
- Add `test_tmpdir_is_in_the_policy_and_outside_the_repo(cli, tmp_path, monkeypatch)`: after `_create()` in the `layout` fixture, the written `openshell-policy.yaml` lists `osh.TMPDIR` under `filesystem_policy.read_write`; `Path(osh.TMPDIR)` is not the image's folder and the folder is not among its parents; `"/tmp"` is still listed.

The fake CLI's "VM" is the host, so these commands run with `TMPDIR=/var/tmp` on the host. None of them writes a temp file. Don't add one that does.

### 5. `.claude/skills/sssf/tests/test_openshell_gateway.py` (new)

Same header and run line as `tests/test_openshell_setup_refuses.py`. Every test uses a temp `HOME` and a temp `bin` first on `PATH` with fakes for `openshell`, `openshell-gateway` and `systemctl` that log their call to a file. Nothing real is listed, restarted or written outside `tmp_path`. The fakes take their behaviour from env vars:

- `openshell sandbox list` prints `$FAKE_SANDBOXES` and exits `$FAKE_LIST_CODE` (default 0).
- `openshell-gateway config preflight` exits `$FAKE_PREFLIGHT_CODE` (default 0).
- `systemctl --user show ...` prints `$FAKE_ACTIVE_ENTER` and exits `$FAKE_SHOW_CODE` (default 0). It also logs the `TZ` it was given.

Two helpers give the fake its start time from the file's own mtime, formatted as `Fri 2026-11-20 16:13:05 UTC` in UTC: `started_before(conf)` is the mtime minus an hour, `started_after(conf)` is the mtime plus an hour. No test plants a marker, and no test sleeps.

Run the script as `[sys.executable, "-I", SCRIPT, ...]` with `SSSF_ADW_ID` and `SSSF_AGENT` removed from the env, as `environment()` in the neighbouring file does.

Script tests (not `host_only`):

- **Size, fresh.** Starting from the exact bytes the recipe's `printf` writes, `size` gives exactly the bytes shown above and exits 0. Nothing else is created under the temp `HOME`.
- **Size, existing.** A file with a top comment, an extra key in `[openshell.gateway]`, another table (`[openshell.drivers.docker]` with a key), and an `[openshell.drivers.vm]` table holding `mem_mib = 2048  # mine` and an unrelated key, plus CRLF on one line. With `SSSF_OPENSHELL_MEM_MIB=8192`: only the `2048` becomes `8192` (comment and line ending kept), `vcpus = 4` and `overlay_disk_mib = 16384` are added after the header, and removing those two lines and reverting that one value gives back the original bytes. The output names each changed size.
- **Second run.** `size` again with the same env: exit 0, the bytes and `st_mtime_ns` are the same, the output says unchanged. Then `restart` with `started_after(conf)`: no `openshell` call, no `systemctl restart`.
- **A file value is kept.** A file with `vcpus = 6` and no override keeps 6 and gains the other two defaults.
- **A bad file value.** `vcpus = "many"` or `vcpus = 0` becomes 4, and the output says so.
- **Overrides.** Each of the three env vars sets its size. Parametrized over the three names and the values `0`, `-1`, `1.5`, `abc`, `4 `, `٤`: exit non-zero, stderr names the variable, the file's bytes and mtime are unchanged. An empty value is treated as unset.
- **A layout it can't edit.** `drivers = { vm = { vcpus = 2 } }` inline under `[openshell]`: exit non-zero, bytes unchanged.
- **Preflight refuses.** `FAKE_PREFLIGHT_CODE=1`: exit non-zero, bytes unchanged, no temp file left beside `gateway.toml`.
- **No restart under a live run.** After a `size` that changed the file, `started_before(conf)` and `FAKE_SANDBOXES="sssf-abc12345-0a1b"`: `restart` exits 0, the log has no `systemctl --user restart`, stderr names the sandbox and says to rerun `just openshell-setup`. Then with `FAKE_SANDBOXES=""` and nothing else changed: the log has exactly one `systemctl --user restart openshell-gateway`.
- **The list fails.** `started_before(conf)`, `FAKE_LIST_CODE=1`: no `systemctl --user restart`, exit 0, stderr says it wasn't restarted.
- **The gateway started after the file.** `started_after(conf)`: no `openshell` call and no `systemctl --user restart`, whatever `FAKE_SANDBOXES` holds.
- **The same second.** `FAKE_ACTIVE_ENTER` set to the file's own mtime, floored: nothing is listed or restarted.
- **A start time it can't read.** Parametrized over an empty `FAKE_ACTIVE_ENTER`, `n/a`, `Fri 2026-11-20 08:13:05 PST` and `FAKE_SHOW_CODE=1`: no `openshell` call, no `systemctl --user restart`, exit 0, stderr says the gateway was not restarted.
- **The call asks for UTC.** The logged `show` call ran with `TZ=UTC` and names `ActiveEnterTimestamp` and `openshell-gateway`.
- **Inside a run.** With `SSSF_ADW_ID=abc12345`, both subcommands exit non-zero with one stderr line, call nothing, and leave the file as it was.
- **The header matches the imports.** The script's PEP 723 `dependencies` is `[]` and every module it imports is in `sys.stdlib_module_names`.

Recipe tests (`@needs_just` and `@pytest.mark.host_only`, with the same comment line the neighbouring recipe tests carry). They use `recipe_repo`-style setup with the shipped `templates/justfile`, and a fake `uv` that logs its call and, when its arguments are `run .claude/skills/sssf/scripts/openshell_gateway.py ...`, runs the real script with the test's Python:

- **Fresh, through the recipe.** No `gateway.toml`, `FAKE_ACTIVE_ENTER` an hour in the future: the recipe exits 0, the file has exactly the bytes above, and the log has `systemctl --user enable --now openshell-gateway` and no `restart`.
- **Live sandbox, through the recipe.** An existing file with no table, `FAKE_ACTIVE_ENTER` in the year 2000, `FAKE_SANDBOXES` set: exit 0, the file has the sizes, the log has no `restart`, the output says to rerun. Run it again with `FAKE_SANDBOXES=""`: one `restart`, and the file's bytes are unchanged. A third run with `FAKE_ACTIVE_ENTER` an hour in the future, standing for the restarted gateway: no further `restart`, bytes unchanged.

### 6. `.claude/skills/sssf/tests/test_openshell_setup_refuses.py`

No change is needed: its stand-in `uv` exits 0 without running the script, and the recipe still writes the fresh `gateway.toml` itself. Run it to prove that. Edit it only if it fails, and then only the assertion that fails.

### 7. Docs

- `.claude/skills/sssf/references/config.md`, section "A local sandbox: `--sandbox openshell`":
  - Replace "It creates TLS keys and `gateway.toml` only when they're missing." with: TLS keys only when missing; `gateway.toml` is created when missing, and in an existing one setup changes only the three sizes and keeps everything else.
  - Add a paragraph **How big a VM is.** Every sandbox gets 4 vCPUs, 4096 MiB of RAM and a disk of up to 16384 MiB, from `[openshell.drivers.vm]` in `~/.config/openshell/gateway.toml` (`vcpus`, `mem_mib`, `overlay_disk_mib`). The size is the gateway's, so it is the same for every repo on the machine; `openshell sandbox create --cpu/--memory` changes nothing under the VM driver. Change one with `SSSF_OPENSHELL_VCPUS`, `SSSF_OPENSHELL_MEM_MIB` or `SSSF_OPENSHELL_OVERLAY_DISK_MIB` on `just openshell-setup`; an unset one keeps the file's value. The gateway reads the file only when it starts, and a restart stops every sandbox. So setup restarts it only when `gateway.toml` is newer than the running gateway and no sandbox is listed; with a sandbox live it says so, and you rerun setup once the runs end. Your own edit to `gateway.toml` is picked up the same way. Each live VM can use that much RAM and up to that much host disk, so size for the two or three VMs you run at once.
  - Add to the paragraph on env values, or beside it: every command in the VM gets `TMPDIR=/var/tmp`, which is on the VM's disk, because `/tmp` is a tmpfs taken from the VM's RAM. `/tmp` is still writable for tools that ignore `TMPDIR`.
- `.claude/skills/sssf/templates/justfile`: the recipe's comment (above).
- `docs/designs/openshell-sandbox.md`, decision 8: `read_write` on `/tmp`, `/var/tmp` and the baked parent folder.
- `docs/guide/run.md`, the recipe table's `openshell-setup` row: name `scripts/openshell_gateway.py` beside `scripts/openshell_image.py`.
- `docs/open.md`: delete the row that starts "The hub's suite doesn't fit the OpenShell VM". Leave the rows on the suite's temp footprint, on cleanup and on `openshell-setup`'s three gaps.
- No CHANGELOG exists. No `app_docs/` edit: if an older write-up there says setup writes `gateway.toml` only when missing, the documenter notes that this change adds the sizes.

## Threat model

- **An agent in the VM** runs as `ubuntu`. It can write `/var/tmp` as it can `/tmp`: fill the VM's disk, or plant a file there for a later command in the same VM. That only reaches its own VM, which is deleted with the run. Nothing under `/var/tmp` comes back to the host; `handoff_out` and `commits_out` read only the handoff folder and git. It can set its own `TMPDIR` inside a command it runs; SSSF's next exec sets `/var/tmp` again. It cannot reach the gateway, `gateway.toml` or `systemctl`: they are on the host and the VM's network is closed.
- **A bigger VM** lets that agent use up to 4 host CPUs, 4 GiB of RAM and 16 GiB of disk, where it had 2, 2 and 4. That is the point of the change, and the size bounds it.
- **An agent on the host** (`sandbox: none`) runs as the engineer's own user. The script's `SSSF_ADW_ID` refusal stops a direct call only. Such an agent can clear the variable, edit `gateway.toml`, or run `systemctl --user restart openshell-gateway` itself and stop other projects' sandboxes. It can also touch `gateway.toml`, so the engineer's next setup restarts the gateway; that restart still waits for no sandbox to be listed, and the agent could restart it directly anyway. It could do all of that at the baseline too; this guard can't stop it at its level.
- **The restart check races.** A sandbox created between the list and the restart is stopped. The window is one CLI call wide and only the engineer runs setup.

## Baseline

`main` at `3f3b9f9`. Every finding is judged against that commit. There: `policy()` lists `["/tmp", "/dev/null", <folder>]` read-write; no exec sets `TMPDIR`; `openshell-setup` writes `gateway.toml` only when missing, with no driver table, and never restarts a running gateway.

## Bar

Must:

- **Size, fresh.** With no `gateway.toml`, setup writes one with the gateway table as at the baseline and the three sizes at their defaults: the exact bytes above.
- **Size, existing.** Every other key, table, comment and line ending is kept byte for byte. A second run changes nothing, not even the file's mtime, and restarts nothing.
- **Overrides.** Each size can be set; a value that isn't a positive whole number is refused with the setting's name, with nothing written.
- **Restart only when owed.** The gateway is restarted only when `gateway.toml` is newer than the gateway's start. When it started after the file, or in the same second, nothing is listed and nothing is restarted.
- **No restart under a live run.** With any sandbox listed, when the list fails, or when the start time can't be read, nothing is restarted and setup says what to do. A rerun once no sandbox is listed restarts it, with no state kept between the runs.
- **TMPDIR.** Every `sandbox exec` carries `TMPDIR=/var/tmp` in the stdin frame, the policy lists `/var/tmp` read-write, and it is outside the repo's tree.
- **Nothing else changes.** Every existing test passes, with one assertion altered (`read_write` in `test_the_default_policy`). The policy's other paths, `include_workdir`, Landlock setting and network rules are as at the baseline.

Must never, against the baseline:

- Keep an agent's file, run an agent's code, or delete or overwrite an engineer's file. `gateway.toml` is the one engineer file touched, only its three sizes, only after the parsed-document check and the gateway's own preflight pass, and by an atomic replace.
- Write any file of its own besides `gateway.toml`: no marker, no state file.
- Put an env value on a command line. `TMPDIR` travels in the frame like every other value.
- Take the folder for `TMPDIR` from the host's env, the repo or an agent.
- Restart, stop or delete anything when a sandbox is listed; run `sudo`; run inside a run.
- Start a real VM, gateway, `systemctl` or agent in a test.

## Out of scope

Old holes and limits this change leaves open:

- A host agent that clears `SSSF_ADW_ID`, or runs the steps by hand, can still run setup, edit `gateway.toml` or restart the gateway (see config.md).
- The race between listing sandboxes and restarting.
- `enable --now` doesn't restart a running gateway after the unit file changes, for example a moved `openshell-gateway`. Only a newer `gateway.toml` triggers a restart.
- A config write in the same second as the gateway's start, after it, is not seen as owed. Setup can't produce that order.
- A `systemctl` that prints the start time in another shape: setup restarts nothing and tells the engineer to do it by hand.
- Tools that ignore `TMPDIR` still write to the `/tmp` tmpfs, which grows with the VM's RAM.
- An agent can fill its VM's disk, which is up to 16 GiB of host disk per VM.
- Shrinking the suite's temp footprint, and SSSF's cleanup of images, VM disks and sessions (their own `docs/open.md` rows).
- Per-repo VM sizes, and the remote modes (`vercel`, `e2b`), which get no `TMPDIR` change.
- `openshell-setup`'s three older gaps (gateway registration, old images, quiet output) in their `docs/open.md` row.

## Steps for the builder

1. Write `.claude/skills/sssf/scripts/openshell_gateway.py`.
2. Edit the `openshell-setup` recipe and its comment in `.claude/skills/sssf/templates/justfile`.
3. Edit `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`.
4. Edit `.claude/skills/sssf/tests/test_sandbox_openshell.py` and add `.claude/skills/sssf/tests/test_openshell_gateway.py`.
5. Edit the docs in section 7, and delete the `docs/open.md` row.
6. Run the verification.

Do not touch `justfile`, `adws/`, `specs/` or `app_docs/`.

## Verification

From `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_openshell_gateway.py tests/test_sandbox_openshell.py tests/test_openshell_setup_refuses.py -m 'not pushes and not host_only'
```

It must exit 0. The whole suite, and the `host_only` recipe tests, are verify's. No test starts a real VM, gateway or agent.

Also check by reading: `git diff 3f3b9f9 -- .claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` shows only the constant, the `read_write` list, the two `_frame` calls and the docstring.

## For the engineer

- The hub's own copies are protected and the builder leaves them: `justfile` (its `openshell-setup` recipe) and `adws/adw_modules/sandbox_openshell.py`. After the merge, update the stamped copies (`install.py --update`), the hub's and each project's. Until a repo is updated, its runs set no `TMPDIR` and its policy has no `/var/tmp`.
- `scripts/openshell_gateway.py` and `tests/test_openshell_gateway.py` are new and not in `protected_files`. The request says the setup recipe isn't guard code; add them to `protected_files` and `guard_builder`'s `writes` if you want them pinned.
- The proof, with no runs live:
  1. `just openshell-setup`. It should print the three sizes it set and that it restarted the gateway. If it prints "Couldn't read when the gateway started" instead, this machine's `systemctl` prints the time in a shape the script doesn't read: restart the gateway by hand and file the text it printed. `TZ=UTC LC_ALL=C systemctl --user show -p ActiveEnterTimestamp --value openshell-gateway` shows it.
  2. In a throwaway sandbox: `nproc` (4), `free -m` (about 4096), `df -h / /tmp /var/tmp` (`/var/tmp` on the root disk of about 16G, not a tmpfs), and `echo $TMPDIR` through an SSSF run's command.
  3. Sparse or not, the check I couldn't make: with that sandbox up, find its overlay file under `~/.local/state/openshell/vm-driver/` and compare `du -h --apparent-size <file>` with `du -h <file>`. If they match at about 16G, the file is not sparse: lower `SSSF_OPENSHELL_OVERLAY_DISK_MIB` or count 16 GiB a live VM, and correct config.md.
  4. `/var/tmp` in the VM. If it is a tmpfs there, `TMPDIR` gains nothing. If it is missing, it may be worse: the policy sets Landlock to `hard_requirement`, and if OpenShell refuses a policy path that doesn't exist, every `sandbox create` fails until the path is taken out. Either way the fix is a folder the image makes and owns, which changes every image tag; file it and don't ship this TMPDIR half.
  5. Rerun `specs/tasks/flaky-obs-retry-timing.md` with `just simple-sdlc --worktree --sandbox openshell` and see verify pass in the VM.
