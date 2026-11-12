/** The factory board's project rows, from `check_projects.py --json`. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { FactoryLevel, FactoryRow } from "../shared/types.ts";

export interface CheckProjectsRepo {
  repo: string;
  branch?: string | null;
  checks: { name: string; ok: boolean; note?: string | null }[];
  root?: string;
  stamped?: string | null;
  behind?: boolean | null;
  at_stamp?: boolean | null;
  ok?: boolean;
}
export interface CheckProjectsJson { skill?: string; hidden?: number; repos: CheckProjectsRepo[]; }

export function parseCheckProjects(stdout: string): CheckProjectsJson {
  const data = JSON.parse(stdout) as CheckProjectsJson;
  if (!data || !Array.isArray(data.repos)) throw new Error("no repos array in the output");
  return data;
}

/** The `sandbox:` key of a repo's repo.yaml, or "none". */
export function readSandbox(root: string): string {
  try {
    const data = Bun.YAML.parse(readFileSync(join(root, "adws", "adw_sssf_config", "repo.yaml"), "utf8")) as Record<string, unknown> | null;
    const value = data?.sandbox;
    return typeof value === "string" && value ? value : "none";
  } catch {
    return "none";
  }
}

export type ImageState = { built: boolean } | { error: string } | null;

export function projectRow(
  repo: CheckProjectsRepo,
  { hubSkill, sandbox, image }: { hubSkill: string; sandbox: string; image: ImageState },
): FactoryRow {
  const red: string[] = [];
  const amber: string[] = [];
  const notes: string[] = [];
  let next: string | null = null;
  let adw_id: string | null = null;
  const name = repo.repo;
  const setNext = (n: string) => { next ??= n; };
  const check = (n: string) => repo.checks.find((c) => c.name === n);

  const git = check("repo");
  if (git && !git.ok) { red.push(`not a git repo${git.note ? `: ${git.note}` : ""}`); setNext(`ask the ${name} session why ${repo.root ?? "its root"} isn't a git repo`); }
  const clean = check("clean");
  if (clean && !clean.ok) { red.push(`dirty tree on ${repo.branch ?? "its branch"}: ${clean.note ?? ""}`.trim()); setNext(`ask the ${name} session to commit or stash its changes`); }
  if (repo.at_stamp === false) { red.push(`its committed skill doesn't match the hub's at its stamp ${repo.stamped ?? "(none)"}`); setNext(`ask the ${name} session what changed in \`.claude/skills/sssf\``); }
  const quality = check("quality");
  if (quality && !quality.ok) {
    const note = quality.note ?? "";
    const m = /^(\S+) (\S+)$/.exec(note);
    if (note.startsWith("no ")) {
      amber.push("no quality run has passed since its last update");
      setNext(`ask the ${name} session to run \`just quality\``);
    } else if (m && m[2] !== "success" && m[2] !== "running") {
      adw_id = m[1];
      red.push(`last quality run ${m[1]} ${m[2] === "fail" ? "failed" : m[2]}`);
      setNext(`ask the ${name} session to look at run ${m[1]}`);
    }
  }

  if (repo.behind === true) {
    amber.push(`skill ${repo.stamped ?? "(none)"} is behind the hub's ${hubSkill}`);
    setNext(`ask the ${name} session to update to skill ${hubSkill}`);
    if (repo.at_stamp === null || repo.at_stamp === undefined) notes.push("the hub doesn't know its stamp commit");
  }
  const commit = check("commit");
  if (commit && !commit.ok && repo.behind !== true) amber.push(`the update commit doesn't name skill ${hubSkill}`);

  notes.push(`sandbox: ${sandbox}`);
  if (sandbox === "openshell") {
    if (image && "built" in image) {
      notes.push(image.built ? "image built" : "image not built");
      if (!image.built) { amber.push("its OpenShell image isn't built"); setNext(`ask the engineer to run \`just openshell-setup\` in ${name}`); }
    } else {
      notes.push(`image: not checked (${image && "error" in image ? image.error : "no result"})`);
    }
  }

  const level: FactoryLevel = red.length ? "red" : amber.length ? "amber" : "ok";
  const problems = [...red, ...amber];
  return {
    section: "projects", key: `projects/${name}`, level, title: name, problems,
    figure: problems.length ? problems[0] : null,
    next: level === "ok" ? null : next, repo: name, adw_id, notes,
  };
}
