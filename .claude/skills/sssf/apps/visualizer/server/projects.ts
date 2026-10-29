/**
 * The projects one visualizer serves.
 *
 * `just obs` serves one repo: the one whose sssf.db it was pointed at.
 * `just obs-all` (--all) serves every repo in the registry that install.py
 * writes: ~/.config/sssf/projects.json, or $SSSF_REGISTRY. The registry is
 * read again on every listing, so a repo installed while the dashboard runs
 * shows up without a restart.
 *
 * A project's db opens on first use and stays open. A repo with no runs yet
 * has no db file; it lists with available=false until its first run.
 */
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, resolve, sep } from "node:path";
import { SssfDb } from "./db.ts";

export interface Project {
  name: string;
  root: string;
  dbPath: string;
}

const DB_SUFFIX = join("adws", "adw_data", "sssf.db");

export function registryPath(): string {
  if (process.env.SSSF_REGISTRY) return process.env.SSSF_REGISTRY;
  const config = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
  return join(config, "sssf", "projects.json");
}

/** Every registered project. A missing or broken registry reads as empty. */
export function registered(): Project[] {
  const path = registryPath();
  if (!existsSync(path)) return [];
  try {
    const data = JSON.parse(readFileSync(path, "utf8")) as {
      projects?: Record<string, { root?: string; db?: string }>;
    };
    return Object.entries(data.projects ?? {})
      .filter(([, entry]) => typeof entry.root === "string")
      .map(([name, entry]) => {
        const root = entry.root as string;
        const db = entry.db ?? DB_SUFFIX;
        return { name, root, dbPath: isAbsolute(db) ? db : resolve(root, db) };
      });
  } catch (error) {
    console.warn(`[sssf] could not read ${path}: ${(error as Error).message}`);
    return [];
  }
}

/** The project for one db path, named as the registry names it when it can be. */
export function projectForDb(dbPath: string): Project {
  const root = dbPath.endsWith(sep + DB_SUFFIX)
    ? dbPath.slice(0, -(DB_SUFFIX.length + 1))
    : dirname(dbPath);
  const known = registered().find((p) => p.root === root);
  return { name: known?.name ?? basename(root), root, dbPath };
}

const roots = new Map<string, string | null>();

/** The nearest folder at or above `dir` with a .git entry; looked up once per folder. */
function gitRoot(dir: string): string | null {
  const known = roots.get(dir);
  if (known !== undefined) return known;
  const parent = dirname(dir);
  const found = existsSync(join(dir, ".git")) ? dir : parent === dir ? null : gitRoot(parent);
  roots.set(dir, found);
  return found;
}

export class Projects {
  private readonly dbs = new Map<string, SssfDb>();

  /** `only` set: serve that one project. Unset: serve the registry. */
  constructor(private readonly only: Project | null) {}

  get all(): boolean {
    return this.only === null;
  }

  list(): Project[] {
    return this.only ? [this.only] : registered();
  }

  find(name: string): Project | undefined {
    return this.list().find((p) => p.name === name);
  }

  /** The project's db, opened on first use; null while the repo has no runs. */
  db(project: Project): SssfDb | null {
    const open = this.dbs.get(project.dbPath);
    if (open) return open;
    if (!existsSync(project.dbPath)) return null;
    const db = new SssfDb(project.dbPath);
    this.dbs.set(project.dbPath, db);
    return db;
  }

  /**
   * Every registered repo, plus the one being served. The account spans all
   * of them, so even one-repo `just obs` reads them all for it.
   */
  everyProject(): Project[] {
    const all = registered();
    if (this.only && !all.some((p) => p.dbPath === this.only?.dbPath)) all.push(this.only);
    return all;
  }

  /**
   * The registered project whose root holds `cwd`. Otherwise the git repo that
   * holds it, so work in a subfolder counts toward its repo, and otherwise the
   * folder's own name.
   */
  projectOf(cwd: string): string {
    const match = this.everyProject()
      .filter((p) => cwd === p.root || cwd.startsWith(p.root + sep))
      .toSorted((a, b) => b.root.length - a.root.length)[0];
    if (match) return match.name;
    return basename(gitRoot(cwd) ?? cwd) || "(unknown)";
  }

  close(): void {
    for (const db of this.dbs.values()) db.close();
  }
}
