// SSSF's obs relay, run in a remote VM for one pi call
// (docs/designs/remote-sandboxes.md, decisions 37 to 50).
//
// The pi-observability extension posts its events here instead of to the
// engineer's obs server, which the VM can't reach and whose token stays on
// the host. Each event goes to the file named in argv[2], one JSON line each,
// before the post is answered. The relay's one line of stdout, once it
// listens, is {"sssf_obs_relay": <port>}.
//
// `--follow <file>` is the live stream instead: it copies the file's lines to
// stdout as they come, and reads on only once stdout has taken the last
// write, so a host that stops reading holds up the follower alone, never the
// relay, and the follower holds no more than one line. A line longer than
// LINE_BYTES is skipped. The host also reads the file whole after the call.
//
// No dependencies: it runs with the Node the VM already has for pi.
"use strict";
const http = require("node:http");
const fs = require("node:fs");

const MAX_REQUEST_BYTES = 4 * 1024 * 1024;   // obs's own limit
const LINE_BYTES = 3 * 1024 * 1024;          // the host can't post a longer line

if (process.argv[2] === "--follow") follow(process.argv[3]);
else relay(process.argv[2]);

function follow(path) {
  const chunk = Buffer.alloc(64 * 1024);
  let fd = null;
  let offset = 0;
  let line = [];
  let size = 0;
  let skipping = false;
  const later = (ms) => new Promise((r) => setTimeout(r, ms));
  const put = (buf) => new Promise((r) => (process.stdout.write(buf) ? r() : process.stdout.once("drain", r)));
  (async () => {
    for (;;) {
      if (fd === null) {
        try { fd = fs.openSync(path, "r"); } catch { await later(100); continue; }
      }
      const got = fs.readSync(fd, chunk, 0, chunk.length, offset);
      if (got === 0) { await later(100); continue; }
      offset += got;
      let start = 0;
      for (let i = 0; i < got; i++) {
        if (chunk[i] !== 10) continue;
        const piece = chunk.subarray(start, i + 1);
        start = i + 1;
        if (!skipping && size + piece.length <= LINE_BYTES) {
          line.push(Buffer.from(piece));
          await put(Buffer.concat(line));
        }
        line = []; size = 0; skipping = false;
      }
      const rest = chunk.subarray(start, got);
      if (skipping || size + rest.length > LINE_BYTES) {
        skipping = true; line = []; size = 0;
      } else if (rest.length) {
        line.push(Buffer.from(rest)); size += rest.length;
      }
    }
  })();
}

function relay(file) {

function answer(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(body === undefined ? "" : JSON.stringify(body));
}

const server = http.createServer((req, res) => {
  const path = (req.url || "").split("?")[0];
  if (req.method === "GET" && path === "/health") return answer(res, 200, { ok: true });
  if (req.method !== "POST" || path !== "/events") return answer(res, 404, { error: "not found" });
  const chunks = [];
  let size = 0;
  let over = false;
  req.on("data", (chunk) => {
    if (over) return;
    size += chunk.length;
    if (size > MAX_REQUEST_BYTES) {
      over = true;
      answer(res, 413, { error: "request too large" });
      req.resume();
      return;
    }
    chunks.push(chunk);
  });
  req.on("end", () => {
    if (over) return;
    let parsed;
    try {
      parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      return answer(res, 400, { error: "invalid JSON" });
    }
    const events = Array.isArray(parsed) ? parsed : [parsed];
    if (events.length) {
      const text = events.map((e) => JSON.stringify(e)).join("\n") + "\n";
      try {
        fs.appendFileSync(file, text);
      } catch (err) {
        return answer(res, 500, { error: String(err && err.message) });
      }
    }
    answer(res, 200, { ingested: events.length, rejected: [] });
  });
});

fs.writeFileSync(file, "", { flag: "a" });
server.listen(0, "127.0.0.1", () => {
  process.stdout.write(JSON.stringify({ sssf_obs_relay: server.address().port }) + "\n");
});
}
