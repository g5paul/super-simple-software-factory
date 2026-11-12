/**
 * The factory board's machine rows: memory, leftover test processes, the
 * agentsh store and prune, slow polls, and VMs left open. Pure readers: each
 * takes its root, path and clock as a parameter, so tests use a fake /proc.
 */
import { existsSync, readFileSync, readdirSync, readlinkSync } from "node:fs";
import { basename, join } from "node:path";
import type { FactoryLevel, FactoryRow, LeftoverProcess } from "../shared/types.ts";
import {
  CLK_TCK, GIB, MEM_AMBER, MEM_RED, POLL_AMBER_S, POLL_RED_S, PRUNE_AMBER_H, PRUNE_RED_H, STORE_AMBER, STORE_RED,
} from "./factory_limits.ts";

function machineRow(key: string, title: string, level: FactoryLevel, extra: Partial<FactoryRow> = {}): FactoryRow {
  return { section: "machine", key: `machine/${key}`, level, title, problems: [], figure: null, next: null, repo: null, adw_id: null, notes: [], ...extra };
}

const gib = (bytes: number) => `${(bytes / GIB).toFixed(1)} GiB`;

export function readMeminfo(procRoot: string): { available: number | null } {
  try {
    const m = /^MemAvailable:\s+(\d+)\s*kB/m.exec(readFileSync(join(procRoot, "meminfo"), "utf8"));
    return { available: m ? Number(m[1]) * 1024 : null };
  } catch {
    return { available: null };
  }
}

export function memoryRow(available: number | null): FactoryRow {
  if (available === null) {
    return machineRow("memory", "Memory", "amber", { problems: ["could not read /proc/meminfo"] });
  }
  const level: FactoryLevel = available < MEM_RED ? "red" : available < MEM_AMBER ? "amber" : "ok";
  return machineRow("memory", "Memory", level, {
    figure: `${gib(available)} free`,
    problems: level === "ok" ? [] : [`only ${gib(available)} of memory is available`],
    next: level === "ok" ? null : "close what holds memory; see the leftover processes row",
  });
}

export interface ProcInfo { pid: number; argv: string[]; cwd: string | null; age_s: number | null; }

export function scanProcesses(procRoot: string, { uid, selfPid }: { uid: number; selfPid: number }): ProcInfo[] {
  let uptime: number | null = null;
  try {
    const n = Number(readFileSync(join(procRoot, "uptime"), "utf8").trim().split(/\s+/)[0]);
    uptime = Number.isFinite(n) ? n : null;
  } catch { /* age unknown */ }
  let names: string[] = [];
  try { names = readdirSync(procRoot).filter((n) => /^\d+$/.test(n)); } catch { /* no /proc */ }
  const out: ProcInfo[] = [];
  for (const name of names) {
    const pid = Number(name);
    if (pid === selfPid) continue;
    try {
      const dir = join(procRoot, name);
      const owner = /^Uid:\s+(\d+)/m.exec(readFileSync(join(dir, "status"), "utf8"));
      if (!owner || Number(owner[1]) !== uid) continue;
      const argv = readFileSync(join(dir, "cmdline"), "utf8").replace(/\0+$/, "").split("\0");
      let cwd: string | null = null;
      try { cwd = readlinkSync(join(dir, "cwd")); } catch { /* not readable */ }
      let age: number | null = null;
      try {
        const stat = readFileSync(join(dir, "stat"), "utf8");
        const start = Number(stat.slice(stat.lastIndexOf(") ") + 2).split(" ")[19]);
        if (uptime !== null && Number.isFinite(start)) age = Math.max(0, uptime - start / CLK_TCK);
      } catch { /* age unknown */ }
      out.push({ pid, argv, cwd, age_s: age });
    } catch { /* exited, or not ours to read */ }
  }
  return out;
}

export function isPytest(argv: string[]): boolean {
  return argv.some((a, i) => ["pytest", "py.test"].includes(basename(a)) || (a === "-m" && argv[i + 1] === "pytest"));
}

export function leftoverRow(procs: ProcInfo[], { tmpdir, user }: { tmpdir: string; user: string }): FactoryRow {
  const dirs = [...new Set([`${tmpdir.replace(/\/+$/, "")}/pytest-of-${user}`, `/tmp/pytest-of-${user}`])];
  const inside = (p: string | null) => p !== null && dirs.some((d) => p === d || p.startsWith(d + "/"));
  const title = "Leftover test processes";
  if (procs.some((p) => isPytest(p.argv))) {
    return machineRow("leftovers", title, "ok", { notes: ["a pytest run is in progress; not checked"] });
  }
  const left = procs.filter((p) => inside(p.cwd) || p.argv.some(inside)).toSorted((a, b) => a.pid - b.pid);
  if (!left.length) return machineRow("leftovers", title, "ok");
  const processes: LeftoverProcess[] = left.map((p) => ({
    pid: p.pid, age_s: p.age_s, cwd: p.cwd, command: p.argv.join(" ").slice(0, 200),
  }));
  return machineRow("leftovers", title, "red", {
    figure: `${left.length} processes`,
    problems: [`${left.length} processes still run from a pytest temp dir`],
    next: `list them with \`ps -o pid,etime,args -p ${left.map((p) => p.pid).join(",")}\` and kill them by hand; the page never kills`,
    processes,
  });
}

export interface PruneStatus {
  store_bytes: number | null; audit_bytes: number | null;
  last_prune: string | null; last_analyze: string | null; last_run: string | null;
}

export function readPruneStatus(path: string): PruneStatus | null {
  try {
    if (!existsSync(path)) return null;
    const data = JSON.parse(readFileSync(path, "utf8")) as Partial<PruneStatus> | null;
    if (!data || typeof data !== "object") return null;
    return {
      store_bytes: typeof data.store_bytes === "number" ? data.store_bytes : null,
      audit_bytes: typeof data.audit_bytes === "number" ? data.audit_bytes : null,
      last_prune: typeof data.last_prune === "string" ? data.last_prune : null,
      last_analyze: data.last_analyze ?? null,
      last_run: data.last_run ?? null,
    };
  } catch {
    return null;
  }
}

export function pruneRows(status: PruneStatus | null, now: number, path = ""): FactoryRow[] {
  const enable = "sudo systemctl enable --now sssf-agentsh-prune.timer";
  if (!status) {
    const problems = [`no prune status at ${path}`];
    return [
      machineRow("agentsh-store", "agentsh store", "amber", { problems, next: enable }),
      machineRow("agentsh-prune", "agentsh prune", "amber", { problems, next: enable }),
    ];
  }
  const rows: FactoryRow[] = [];
  if (status.store_bytes === null && status.audit_bytes === null) {
    rows.push(machineRow("agentsh-store", "agentsh store", "amber", { problems: ["store size not measured"], figure: "not measured" }));
  } else {
    const total = (status.store_bytes ?? 0) + (status.audit_bytes ?? 0);
    const level: FactoryLevel = total >= STORE_RED ? "red" : total >= STORE_AMBER ? "amber" : "ok";
    rows.push(machineRow("agentsh-store", "agentsh store", level, {
      figure: gib(total),
      problems: level === "ok" ? [] : [`the agentsh store and audit log hold ${gib(total)}`],
      next: level === "ok" ? null : "with no run going, stop agentsh, move /var/lib/agentsh/events.db and its -wal and -shm files aside, start it, and delete them once a run works",
    }));
  }
  const last = status.last_prune ? Date.parse(status.last_prune) : NaN;
  if (!Number.isFinite(last)) {
    rows.push(machineRow("agentsh-prune", "agentsh prune", "amber", { problems: ["never pruned"], figure: "never pruned", next: enable }));
  } else {
    const hours = (now - last) / 3_600_000;
    const level: FactoryLevel = hours > PRUNE_RED_H ? "red" : hours > PRUNE_AMBER_H ? "amber" : "ok";
    rows.push(machineRow("agentsh-prune", "agentsh prune", level, {
      figure: `${hours.toFixed(1)} h ago`,
      problems: level === "ok" ? [] : [`the last prune was ${hours.toFixed(1)} h ago`],
      next: level === "ok" ? null : "check `systemctl status sssf-agentsh-prune.timer`",
    }));
  }
  return rows;
}

export function pollRows(perRepo: { repo: string; events: { at: string; payload: Record<string, unknown> | null }[] }[]): FactoryRow[] {
  const rows: FactoryRow[] = [];
  for (const { repo, events } of perRepo) {
    if (!events.length) continue;
    const slowest = Math.max(0, ...events.map((e) => Number(e.payload?.slowest_poll_seconds) || 0));
    let level: FactoryLevel = slowest > POLL_RED_S ? "red" : slowest > POLL_AMBER_S ? "amber" : "ok";
    const problems: string[] = [];
    if (level !== "ok") problems.push(`agentsh's slowest poll took ${slowest.toFixed(1)} s`);
    let figure: string | null = level === "ok" ? null : `${slowest.toFixed(1)} s slowest poll`;
    const totals = new Map<string, number>();
    let recorded = false;
    for (const e of events) {
      const drops = e.payload?.drops;
      if (drops && typeof drops === "object") {
        recorded = true;
        for (const [k, v] of Object.entries(drops as Record<string, unknown>)) totals.set(k, (totals.get(k) ?? 0) + (Number(v) || 0));
      }
    }
    const notes: string[] = [];
    if (!recorded) notes.push("drops: not recorded");
    else if (totals.size) notes.push(`drops: ${[...totals].map(([k, v]) => `${k} ${v}`).join(", ")}`);
    const ptrace = totals.get("ptrace_execve") ?? 0;
    if (ptrace > 0) {
      if (level === "ok") level = "amber";
      problems.push(`agentsh dropped ${ptrace} ptrace_execve events`);
      figure = `${ptrace} ptrace_execve dropped`;
    }
    rows.push(machineRow(`polls/${repo}`, `agentsh polls in ${repo}`, level, {
      repo, problems, figure, notes,
      next: level === "ok" ? null : `ask the hub session to look at agentsh's poll times in \`${repo}\`'s trace`,
    }));
  }
  return rows;
}

export interface OpenVm { adw_id: string; vm: string | null; provider: string | null; at: string; run_status: string | null; }

export function vmRows(perRepo: { repo: string; open: OpenVm[] }[]): FactoryRow[] {
  const rows: FactoryRow[] = [];
  for (const { repo, open } of perRepo) {
    for (const v of open) {
      const vm = v.vm ?? "(unnamed)";
      const provider = v.provider ?? "its provider";
      const idle = v.run_status !== "running";
      rows.push(machineRow(`vm/${repo}/${v.adw_id}/${vm}`, `VM ${vm} (${repo})`, idle ? "amber" : "ok", {
        repo, adw_id: v.adw_id,
        problems: idle ? [`VM ${vm} on ${provider} has no running SSSF run`] : [],
        figure: idle ? `run ${v.run_status ?? "unknown"}` : null,
        notes: idle ? [] : ["in use by a running run"],
        next: idle ? `delete it in the ${provider} console or CLI, then check \`${repo}\` run ${v.adw_id}` : null,
      }));
    }
  }
  return rows;
}
