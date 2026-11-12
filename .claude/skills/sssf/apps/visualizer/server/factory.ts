/**
 * The factory health board: one list of rows (projects, machine, runs), each
 * red, amber or ok, built from the hub's two --json scripts, the registered
 * repos' traces, /proc and the agentsh prune status. Read-only: the page
 * never kills, deletes or edits anything.
 *
 * This is the one mode that runs commands, and only under --factory: the
 * hub's check_projects.py and phase_times.py, git, and the OpenShell image
 * check. Their results are cached for SCRIPT_TTL_MS.
 */
import { existsSync, readFileSync } from "node:fs";
import * as os from "node:os";
import { join, resolve } from "node:path";
import type { FactoryBoard, FactoryRow } from "../shared/types.ts";
import type { Project, Projects } from "./projects.ts";
import type { SssfDb } from "./db.ts";
import { PRUNE_STATUS, RUN_WINDOW_MS, SCRIPT_TTL_MS } from "./factory_limits.ts";
import {
  leftoverRow, memoryRow, pollRows, pruneRows, readMeminfo, readPruneStatus, scanProcesses, vmRows, type OpenVm,
} from "./factory_machine.ts";
import { parseCheckProjects, projectRow, readSandbox, type CheckProjectsJson, type ImageState } from "./factory_projects.ts";
import { parsePhaseTimes, runRows, type FactoryRunFacts, type PhaseTimesJson } from "./factory_runs.ts";

export * from "./factory_limits.ts";

export class FactoryError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** The path after `--factory`, resolved; null when the flag is absent. */
export function factoryHub(argv: string[]): string | null {
  const i = argv.indexOf("--factory");
  const inline = argv.find((a) => a.startsWith("--factory="));
  if (i === -1 && !inline) return null;
  const value = i !== -1 ? argv[i + 1] : inline?.slice("--factory=".length);
  if (!value || value.startsWith("--")) throw new Error("--factory needs the hub's path");
  return resolve(value);
}

export type Runner = (cmd: string[], cwd: string) => Promise<{ code: number; stdout: string; stderr: string }>;

export const bunRunner: Runner = async (cmd, cwd) => {
  try {
    const proc = Bun.spawn(cmd, { cwd, stdout: "pipe", stderr: "pipe", timeout: 60_000 });
    const [stdout, stderr] = await Promise.all([new Response(proc.stdout).text(), new Response(proc.stderr).text()]);
    return { code: await proc.exited, stdout, stderr };
  } catch (error) {
    return { code: 127, stdout: "", stderr: (error as Error).message };
  }
};

export interface FactoryOptions {
  hub: string;
  projects: Projects;
  runner: Runner;
  procRoot?: string;
  pruneStatus?: string;
  uid?: number;
  selfPid?: number;
  tmpdir?: string;
  user?: string;
  hostname?: string;
  now?: () => number;
}

interface Scripts {
  at: number;
  check: CheckProjectsJson | null;
  phases: PhaseTimesJson | null;
  commonDir: string | null;
  images: Map<string, ImageState>;
  errors: string[];
  failRows: FactoryRow[];
}

interface Cached { at: number; rows: FactoryRow[]; }

const tail = (s: string) => s.trim().slice(-300);

function failRow(section: "projects" | "runs", script: string, why: string): FactoryRow {
  return {
    section, key: `${section}/error/${script}`, level: "red", title: `${script} --json`,
    problems: [`\`${script} --json\` failed: ${why}`], figure: null,
    next: "run `just projects` in the hub", repo: null, adw_id: null, notes: [],
  };
}

export class Factory {
  private readonly o: Required<Omit<FactoryOptions, "uid">> & { uid: number };
  private scriptCache: Scripts | null = null;
  private scriptFlight: Promise<Scripts> | null = null;
  private rowCache: Cached | null = null;

  constructor(options: FactoryOptions) {
    this.o = {
      procRoot: "/proc",
      pruneStatus: PRUNE_STATUS,
      uid: process.getuid?.() ?? -1,
      selfPid: process.pid,
      tmpdir: process.env.TMPDIR ?? "/tmp",
      user: process.env.USER ?? os.userInfo().username,
      hostname: os.hostname(),
      now: () => Date.now(),
      ...options,
    };
  }

  /** Run the hub's scripts, cached; callers in flight share one run. Never throws. */
  scripts(refresh: boolean): Promise<Scripts> {
    const now = this.o.now();
    if (refresh) { this.scriptCache = null; this.rowCache = null; }
    if (this.scriptCache && now - this.scriptCache.at < SCRIPT_TTL_MS) return Promise.resolve(this.scriptCache);
    if (this.scriptFlight) return this.scriptFlight;
    this.scriptFlight = this.runScripts(now).then((s) => {
      this.scriptCache = s;
      this.rowCache = null;
      return s;
    }).finally(() => { this.scriptFlight = null; });
    return this.scriptFlight;
  }

  private async runScripts(at: number): Promise<Scripts> {
    const { hub, runner } = this.o;
    const errors: string[] = [];
    const failRows: FactoryRow[] = [];
    const attempt = async (cmd: string[]) => {
      try { return await runner(cmd, hub); } catch (error) { return { code: 127, stdout: "", stderr: (error as Error).message }; }
    };
    const [checkOut, phaseOut, gitOut] = await Promise.all([
      attempt(["uv", "run", "adws/check_projects.py", "--json"]),
      attempt(["uv", "run", "adws/phase_times.py", "--json"]),
      attempt(["git", "-C", hub, "rev-parse", "--path-format=absolute", "--git-common-dir"]),
    ]);
    // check_projects.py exits 1 whenever any repo fails, so only a parse failure counts.
    let check: CheckProjectsJson | null = null;
    try { check = parseCheckProjects(checkOut.stdout); } catch (error) {
      const why = `${(error as Error).message}${checkOut.stderr ? `: ${tail(checkOut.stderr)}` : ""}`;
      errors.push(`check_projects.py --json failed (exit ${checkOut.code}): ${why}`);
      failRows.push(failRow("projects", "check_projects.py", why));
    }
    let phases: PhaseTimesJson | null = null;
    try { phases = parsePhaseTimes(phaseOut.stdout); } catch (error) {
      const why = `${(error as Error).message}${phaseOut.stderr ? `: ${tail(phaseOut.stderr)}` : ""}`;
      errors.push(`phase_times.py --json failed (exit ${phaseOut.code}): ${why}`);
      failRows.push(failRow("runs", "phase_times.py", why));
    }
    const commonDir = gitOut.code === 0 && gitOut.stdout.trim() ? gitOut.stdout.trim().split("\n")[0] : null;

    const images = new Map<string, ImageState>();
    for (const r of check?.repos ?? []) {
      if (!r.root || readSandbox(r.root) !== "openshell") continue;
      const tag = await attempt(["uv", "run", ".claude/skills/sssf/scripts/openshell_image.py", "--repo", r.root, "--print"]);
      const name = tag.stdout.trim().split("\n")[0];
      if (tag.code !== 0 || !name) { images.set(r.repo, { error: tail(tag.stderr) || `exit ${tag.code}` }); continue; }
      const has = await attempt(["podman", "image", "exists", name]);
      images.set(r.repo, has.code === 0 ? { built: true } : has.code === 1 ? { built: false } : { error: tail(has.stderr) || `exit ${has.code}` });
    }
    return { at, check, phases, commonDir, images, errors, failRows };
  }

  private privateNames(commonDir: string | null): string[] | null {
    if (!commonDir) return null;
    const file = join(commonDir, "sssf-private-names");
    if (!existsSync(file)) return null;
    try {
      return readFileSync(file, "utf8").split("\n").map((l) => l.trim().toLowerCase()).filter((l) => l && !l.startsWith("#"));
    } catch {
      return null;
    }
  }

  private runFacts(db: SssfDb, since: string): FactoryRunFacts[] {
    return db.factoryRuns(since).map((r) => ({
      adw_id: r.adw_id,
      adw_name: r.adw_name ?? "",
      status: r.status,
      started_at: r.started_at,
      ended_at: r.ended_at,
      last_error: r.last_error,
      live: r.has_processes === 0 ? null
        : r.open_pid === null ? false
        : (r.open_host === null || r.open_host === this.o.hostname) && existsSync(join(this.o.procRoot, String(r.open_pid))),
      refusals: r.refusals,
      guard: r.blocking === null ? null : { bypasses: r.bypasses ?? 0, blocking: r.blocking },
      spec_blocked: r.blocked_reviews > 0,
    }));
  }

  async board({ listed, refresh }: { listed: boolean; refresh: boolean }): Promise<FactoryBoard> {
    const scripts = await this.scripts(refresh);
    const now = this.o.now();
    const names = this.privateNames(scripts.commonDir);
    if (listed && names === null) {
      throw new FactoryError("the hub has no .git/sssf-private-names, so the switch can't hide anything", 409);
    }
    const errors = [...scripts.errors];
    const every = this.o.projects.everyProject();

    if (!this.rowCache || now - this.rowCache.at >= SCRIPT_TTL_MS) {
      this.rowCache = { at: now, rows: this.slowRows(scripts, every, errors, now) };
    }
    const rows: FactoryRow[] = [...this.rowCache.rows.filter((r) => r.section === "projects"), ...this.machineRows(every, errors, now), ...this.rowCache.rows.filter((r) => r.section === "runs")];

    const unlistedRepos = new Set<string>();
    const isUnlisted = (repo: string) => {
      const root = every.find((p) => p.name === repo)?.root ?? scripts.check?.repos.find((r) => r.repo === repo)?.root ?? "";
      const hay = `${repo}\n${root}`.toLowerCase();
      return (names ?? []).some((n) => hay.includes(n));
    };
    const kept = listed
      ? rows.filter((r) => {
        if (!r.repo || !isUnlisted(r.repo)) return true;
        unlistedRepos.add(r.repo);
        return false;
      })
      : rows;
    const order = { red: 0, amber: 1, ok: 2 } as const;
    const sorted = kept.map((r, i) => ({ r, i })).sort((a, b) => order[a.r.level] - order[b.r.level] || a.i - b.i).map((x) => x.r);
    return {
      generated_at: new Date(now).toISOString(),
      hub: this.o.hub,
      listed,
      private_names: names !== null,
      hidden: unlistedRepos.size,
      red: sorted.filter((r) => r.level === "red").length,
      amber: sorted.filter((r) => r.level === "amber").length,
      rows: sorted,
      errors,
      scripts_at: new Date(scripts.at).toISOString(),
    };
  }

  /** The projects and runs rows: cached together for the refresh pace. */
  private slowRows(scripts: Scripts, every: Project[], errors: string[], now: number): FactoryRow[] {
    const rows: FactoryRow[] = [...scripts.failRows.filter((r) => r.section === "projects")];
    if (scripts.check) {
      for (const r of scripts.check.repos) {
        rows.push(projectRow(r, {
          hubSkill: scripts.check.skill ?? "?",
          sandbox: r.root ? readSandbox(r.root) : "none",
          image: scripts.images.get(r.repo) ?? null,
        }));
      }
    }
    const since = new Date(now - RUN_WINDOW_MS).toISOString().replace("Z", "+00:00");
    const perRepo: { repo: string; runs: FactoryRunFacts[] }[] = [];
    for (const p of every) {
      try {
        const db = this.o.projects.db(p);
        if (db) perRepo.push({ repo: p.name, runs: this.runFacts(db, since) });
      } catch (error) {
        errors.push(`${p.name}: trace would not open: ${(error as Error).message}`);
      }
    }
    rows.push(...scripts.failRows.filter((r) => r.section === "runs"));
    rows.push(...runRows(perRepo, scripts.phases ?? { repos: [] }));
    return rows;
  }

  private machineRows(every: Project[], errors: string[], now: number): FactoryRow[] {
    const { procRoot, uid, selfPid, tmpdir, user } = this.o;
    const since = new Date(now - RUN_WINDOW_MS).toISOString().replace("Z", "+00:00");
    const polls: Parameters<typeof pollRows>[0] = [];
    const vms: { repo: string; open: OpenVm[] }[] = [];
    for (const p of every) {
      try {
        const db = this.o.projects.db(p);
        if (!db) continue;
        polls.push({
          repo: p.name,
          events: db.agentshPolls(since).map((e) => {
            let payload: Record<string, unknown> | null = null;
            try { payload = e.payload_json ? JSON.parse(e.payload_json) : null; } catch { /* unreadable payload */ }
            return { at: e.at, payload };
          }),
        });
        vms.push({ repo: p.name, open: db.openSandboxes() });
      } catch (error) {
        errors.push(`${p.name}: trace would not open: ${(error as Error).message}`);
      }
    }
    return [
      memoryRow(readMeminfo(procRoot).available),
      leftoverRow(scanProcesses(procRoot, { uid, selfPid }), { tmpdir, user }),
      ...pruneRows(readPruneStatus(this.o.pruneStatus), now, this.o.pruneStatus),
      ...pollRows(polls),
      ...vmRows(vms),
    ];
  }
}

export function factoryHandler(factory: Factory): (req: Request) => Promise<Response> {
  const reply = (data: unknown, status = 200) =>
    new Response(JSON.stringify(data), {
      status,
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });
  return async (req) => {
    const q = new URL(req.url).searchParams;
    try {
      return reply(await factory.board({ listed: q.get("listed") === "1", refresh: q.get("refresh") === "1" }));
    } catch (error) {
      if (error instanceof FactoryError) return reply({ error: error.message }, error.status);
      console.error("[sssf] /api/factory:", error);
      return reply({ error: (error as Error).message }, 500);
    }
  };
}
