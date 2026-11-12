import { afterEach, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { networkInterfaces, tmpdir } from "node:os";
import { join, resolve } from "node:path";

// Starts the real server and checks where it listens: 127.0.0.1 answers, and
// every other address this machine has refuses the connection.

let dir = "";
let child: ReturnType<typeof Bun.spawn> | null = null;

afterEach(() => {
  child?.kill();
  child = null;
  if (dir) rmSync(dir, { recursive: true, force: true });
});

function otherAddresses(): string[] {
  return Object.values(networkInterfaces())
    .flat()
    .filter((a) => a && !a.internal && a.family === "IPv4")
    .map((a) => a!.address);
}

async function answers(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

test("the server listens on 127.0.0.1 and nowhere else", async () => {
  dir = mkdtempSync(join(tmpdir(), "sssf-listen-"));
  writeFileSync(join(dir, "projects.json"), JSON.stringify({ projects: {} }));
  const port = 47000 + Math.floor(Math.random() * 2000);
  child = Bun.spawn(["bun", "run", resolve(import.meta.dir, "index.ts"), "--all"], {
    env: { ...process.env, SSSF_REGISTRY: join(dir, "projects.json"), SSSF_OBS_PORT: String(port) },
    stdout: "pipe",
    stderr: "pipe",
  });
  const reader = (child.stdout as ReadableStream<Uint8Array>).getReader();
  let out = "";
  while (!out.includes("visualizer api")) {
    const { value, done } = await reader.read();
    if (done) throw new Error(`server exited before listening: ${out}`);
    out += new TextDecoder().decode(value);
  }
  expect(out).toContain(`http://127.0.0.1:${port}`);
  expect(await answers(`http://127.0.0.1:${port}/api/health`)).toBe(true);
  for (const address of otherAddresses()) {
    expect(await answers(`http://${address}:${port}/api/health`)).toBe(false);
  }
}, 20000);
