/**
 * SSSF visualizer server — JSON API over one or more repos' sssf.db, plus the
 * built UI when ./dist exists. Reads are read-only; the single write is
 * POST /api/projects/:project/sessions/:adw_id/archive, which sets one review
 * flag on a row.
 *
 * There is no ingest endpoint and no websocket. The data path is
 * agents → sqlite → web ui, and the UI gets there by polling.
 *
 *   bun run server/index.ts                    # one repo: the db below
 *   bun run server/index.ts --db /path/to/repo/adws/adw_data/sssf.db
 *   SSSF_DB=/path/to/sssf.db SSSF_OBS_PORT=4610 bun run server/index.ts
 *   bun run server/index.ts --all              # every repo in the registry
 */
import { existsSync, statSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { SssfDb, resolveDbPath } from "./db.ts";
import { Account, type PiCallAt } from "./account.ts";
import { DAY_MS, daySpend, readDailyCap } from "./day.ts";
import { liveSpend, readLiveCap } from "./live.ts";
import { machineDay, readMachineCap } from "./machine.ts";
import { Projects, projectForDb, registryPath } from "./projects.ts";
import { leaderboard, rosterSha, trialView } from "./trials.ts";
import type {
  AccountUsage,
  AgentPrompts,
  ApiError,
  DaySpend,
  LiveSpend,
  MachineDay,
  HealthResponse,
  PiLimitHit,
  ProjectsResponse,
  RosterRow,
  SessionSummary,
  TrialView,
} from "../shared/types.ts";

// SSSF_OBS_PORT (or PORT) pins the port, and a pinned port that is taken is
// an error. Unpinned, the server takes the first free port from 4600 up, so
// several repos can each run `just obs` at the same time.
const PINNED_PORT = process.env.SSSF_OBS_PORT ?? process.env.PORT;
const FIRST_PORT = 4600;
const PORT_TRIES = 50;
const DIST_DIR = resolve(import.meta.dir, "..", "dist");

const ALL = Bun.argv.includes("--all");

// One repo: its db must exist, so a wrong path fails at startup with the
// reason. All repos: a repo with no runs yet is listed, not an error.
let projects: Projects;
if (ALL) {
  projects = new Projects(null);
} else {
  const only = projectForDb(resolveDbPath());
  try {
    new SssfDb(only.dbPath).close();
  } catch (error) {
    console.error(`[sssf] ${(error as Error).message}`);
    process.exit(1);
  }
  projects = new Projects(only);
}

const account = new Account();

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function notFound(message: string): Response {
  return json({ error: message } satisfies ApiError, 404);
}

/** Guard every handler so a malformed query can't take the server down mid-run. */
function safely(
  handler: (req: Request) => Response | Promise<Response>,
): (req: Request) => Promise<Response> {
  return async (req) => {
    try {
      return await handler(req);
    } catch (error) {
      console.error(`[sssf] ${req.method} ${new URL(req.url).pathname}:`, error);
      return json({ error: (error as Error).message } satisfies ApiError, 500);
    }
  };
}

/**
 * adw_ids and agent names are path segments on disk, so anything that isn't a
 * plain identifier is rejected outright rather than sanitized into something
 * that might still escape the sessions directory.
 */
const SAFE_SEGMENT = /^[A-Za-z0-9._-]+$/;

function isSafeSegment(value: string): boolean {
  return SAFE_SEGMENT.test(value) && value !== "." && value !== "..";
}

function param(req: Request, key: string): string {
  return decodeURIComponent(
    (req as Request & { params: Record<string, string> }).params[key] ?? "",
  );
}

/**
 * Route a request to its project's db. `empty` answers for a project that
 * has no runs yet; without it, that is a 404.
 */
function withDb(
  handler: (db: SssfDb, req: Request) => Response | Promise<Response>,
  empty?: () => Response,
): (req: Request) => Promise<Response> {
  return safely((req) => {
    const name = param(req, "project");
    const project = projects.find(name);
    if (!project) return notFound(`no project ${name}`);
    const db = projects.db(project);
    if (!db) return empty ? empty() : notFound(`no runs yet in ${name}`);
    return handler(db, req);
  });
}

function intQuery(req: Request, key: string, fallback: number): number {
  const raw = new URL(req.url).searchParams.get(key);
  if (raw === null || raw.trim() === "") return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Serve the built SPA if it has been built; otherwise point at the dev server. */
async function serveStatic(req: Request): Promise<Response> {
  const { pathname } = new URL(req.url);

  if (!existsSync(DIST_DIR)) {
    return new Response(
      `SSSF visualizer API is running.\n\n` +
        `No ./dist build found. Run "bun run dev" for the Vite dev server ` +
        `(it proxies /api here), or "bun run build" to serve the UI from this process.\n`,
      { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  // Reject traversal before touching the filesystem.
  const candidate = resolve(join(DIST_DIR, pathname));
  if (candidate === DIST_DIR || candidate.startsWith(DIST_DIR + "/")) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      return new Response(Bun.file(candidate));
    }
  }

  // SPA fallback: breadcrumb routes are client-side.
  const indexHtml = join(DIST_DIR, "index.html");
  if (existsSync(indexHtml)) {
    return new Response(Bun.file(indexHtml), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }
  return notFound("not found");
}

function inUse(err: unknown): boolean {
  return (err as { code?: string } | null)?.code === "EADDRINUSE";
}

function listen(start: (port: number) => ReturnType<typeof Bun.serve>) {
  if (PINNED_PORT) {
    try {
      return start(Number(PINNED_PORT));
    } catch (err) {
      if (!inUse(err)) throw err;
      console.error(`[sssf] port ${PINNED_PORT} is in use. Change SSSF_OBS_PORT, or unset it to take the first free port.`);
      process.exit(1);
    }
  }
  for (let port = FIRST_PORT; port < FIRST_PORT + PORT_TRIES; port++) {
    try {
      return start(port);
    } catch (err) {
      if (!inUse(err)) throw err;
    }
  }
  console.error(`[sssf] ports ${FIRST_PORT}-${FIRST_PORT + PORT_TRIES - 1} are all in use. Set SSSF_OBS_PORT.`);
  process.exit(1);
}

const server = listen((port) => Bun.serve({
  port,
  routes: {
    "/api/health": safely(() => json({ ok: true, all: projects.all })),

    "/api/projects": safely(() =>
      json({
        all: projects.all,
        projects: projects.list().map((p) => {
          const db = projects.db(p);
          const counts = db?.overview();
          return {
            name: p.name,
            root: p.root,
            db: p.dbPath,
            available: db !== null,
            sessions: counts?.sessions ?? 0,
            running: counts?.running ?? 0,
            last_started_at: counts?.last_started_at ?? null,
          };
        }),
      } satisfies ProjectsResponse),
    ),

    // The Claude account behind every project: limits and where spend went.
    "/api/account": safely(() => {
      const now = Date.now();
      const since = new Date(now - 7 * 24 * 3_600_000).toISOString();
      const sessions = new Set<string>();
      const piCalls: PiCallAt[] = [];
      let limitHit: PiLimitHit | null = null;
      for (const p of projects.everyProject()) {
        const db = projects.db(p);
        for (const id of db?.agentSessionIds() ?? []) sessions.add(id);
        for (const c of db?.piCalls(since) ?? []) piCalls.push({ ...c, at: Date.parse(c.at), project: p.name });
        const hit = db?.piLimitHit(since);
        if (hit && (!limitHit || hit.at > limitHit.at)) limitHit = { ...hit, project: p.name };
      }
      const usage = account.summary(now, sessions, (cwd) => projects.projectOf(cwd), piCalls);
      usage.pi.limit_hit = limitHit;
      return json(usage satisfies AccountUsage);
    }),

    "/api/projects/:project/health": withDb((db) =>
      json({
        ok: true,
        db: db.path,
        journal_mode: db.journalMode,
        sessions: db.sessionCount(),
      } satisfies HealthResponse),
    ),

    // The last 24 hours against repo.yaml's max_daily_cost. A repo with no
    // trace yet has spent nothing, and its cap still shows.
    "/api/projects/:project/day": safely((req) => {
      const name = param(req, "project");
      const project = projects.find(name);
      if (!project) return notFound(`no project ${name}`);
      const now = Date.now();
      // The trace writes "+00:00", so the bound does too, for a string compare.
      const since = new Date(now - DAY_MS).toISOString().replace("Z", "+00:00");
      const calls = projects.db(project)?.dayCalls(since) ?? [];
      return json(daySpend(calls, readDailyCap(project.root), now) satisfies DaySpend);
    }),

    // Every registered repo's last 24 hours against limits.yaml's max_daily_cost.
    "/api/machine/day": safely(() => {
      const now = Date.now();
      const since = new Date(now - DAY_MS).toISOString().replace("Z", "+00:00");
      const repos = projects.everyProject().map((p) => ({ name: p.name, calls: projects.db(p)?.dayCalls(since) ?? [] }));
      const { cap, error } = readMachineCap();
      return json(machineDay(repos, cap, error, now) satisfies MachineDay);
    }),

    // What the live runs spend together against repo.yaml's max_live_cost.
    "/api/projects/:project/live": safely((req) => {
      const name = param(req, "project");
      const project = projects.find(name);
      if (!project) return notFound(`no project ${name}`);
      const rows = projects.db(project)?.liveRows() ?? [];
      return json(liveSpend(rows, readLiveCap(project.root)) satisfies LiveSpend);
    }),

    // A trial's attempts side by side, with the panel's scorecard once it ran.
    "/api/projects/:project/trials/:trial": withDb((db, req) => {
      const trial = param(req, "trial");
      const rows = db.trialAttempts(trial);
      if (!rows.length) return notFound(`no trial ${trial}`);
      const [card] = db.trialScorecards(trial);
      return json(trialView(trial, rows, card ?? null, db.trialWinners().get(trial) ?? null) satisfies TrialView);
    }),

    // Which roster does this repo's work best: every scored trial, per roster file.
    "/api/projects/:project/rosters": safely((req) => {
      const name = param(req, "project");
      const project = projects.find(name);
      if (!project) return notFound(`no project ${name}`);
      const db = projects.db(project);
      if (!db) return json([] satisfies RosterRow[]);
      const winners = new Set(db.trialWinners().values());
      return json(leaderboard(db.trialScorecards(), winners, (file) => rosterSha(project.root, file)) satisfies RosterRow[]);
    }),

    "/api/projects/:project/sessions": withDb(
      (db, req) => json(db.sessions(intQuery(req, "limit", 200))),
      () => json([] satisfies SessionSummary[]),
    ),

    "/api/projects/:project/sessions/:adw_id": withDb((db, req) => {
      const detail = db.sessionDetail(param(req, "adw_id"));
      return detail ? json(detail) : notFound(`no session ${param(req, "adw_id")}`);
    }),

    // The one write. Archiving is review triage — it belongs to the reader, not
    // to the run — so it never touches anything a tracer wrote.
    "/api/projects/:project/sessions/:adw_id/archive": {
      POST: withDb(async (db, req) => {
        const adwId = param(req, "adw_id");
        if (!isSafeSegment(adwId)) {
          return json({ error: "invalid adw_id" } satisfies ApiError, 400);
        }
        const body = (await req.json().catch(() => ({}))) as { archived?: unknown };
        const archived = body.archived === undefined ? true : Boolean(body.archived);
        return db.setArchived(adwId, archived)
          ? json({ adw_id: adwId, archived })
          : notFound(`no session ${adwId}`);
      }),
    },

    "/api/projects/:project/sessions/:adw_id/events": withDb((db, req) =>
      json(
        db.events(
          param(req, "adw_id"),
          intQuery(req, "after", 0),
          intQuery(req, "limit", 500),
        ),
      ),
    ),

    "/api/projects/:project/sessions/:adw_id/envelopes": withDb((db, req) =>
      json(db.envelopes(param(req, "adw_id"))),
    ),

    "/api/projects/:project/sessions/:adw_id/gates": withDb((db, req) =>
      json(db.gates(param(req, "adw_id"))),
    ),

    // The exact prompts an agent was sent, read from the session dir. Files are
    // the raw record; the db has no copy of them.
    "/api/projects/:project/sessions/:adw_id/agents/:agent/prompts": withDb(async (db, req) => {
      const adwId = param(req, "adw_id");
      const agent = param(req, "agent");
      if (!isSafeSegment(adwId) || !isSafeSegment(agent)) {
        return json({ error: "invalid adw_id or agent" } satisfies ApiError, 400);
      }
      if (!db.session(adwId)) return notFound(`no session ${adwId}`);

      const dir = resolve(db.sessionsDir, adwId, agent, "prompts");
      // Defense in depth: the segment check already forbids traversal.
      if (dir !== db.sessionsDir && !dir.startsWith(db.sessionsDir + sep)) {
        return json({ error: "invalid path" } satisfies ApiError, 400);
      }

      // A prompt file is absent whenever the agent never ran in this session —
      // a normal state, so it reads as null rather than an error.
      const read = async (name: string): Promise<string | null> => {
        const file = Bun.file(join(dir, `${name}.md`));
        return (await file.exists()) ? await file.text() : null;
      };
      return json({
        system: await read("system"),
        user: await read("user"),
      } satisfies AgentPrompts);
    }),
  },

  fetch(req) {
    const { pathname } = new URL(req.url);
    if (pathname.startsWith("/api/")) return notFound(`no route ${pathname}`);
    return serveStatic(req);
  },
}));

console.log(`[sssf] visualizer api  http://localhost:${server.port}`);
if (ALL) {
  const listed = projects.list();
  console.log(`[sssf] registry        ${registryPath()}  [${listed.length} projects]`);
  for (const p of listed) console.log(`[sssf]   ${p.name.padEnd(20)} ${p.root}`);
  if (!listed.length) console.log(`[sssf] no projects yet: install.py and --update add each repo`);
} else {
  const [only] = projects.list();
  console.log(`[sssf] project         ${only.name}`);
  console.log(`[sssf] db              ${only.dbPath}`);
}
console.log(
  existsSync(DIST_DIR)
    ? `[sssf] serving ui from  ${DIST_DIR}`
    : `[sssf] no ./dist — use "bun run dev" for the Vite dev server on :4601`,
);

process.on("SIGINT", () => {
  projects.close();
  process.exit(0);
});
