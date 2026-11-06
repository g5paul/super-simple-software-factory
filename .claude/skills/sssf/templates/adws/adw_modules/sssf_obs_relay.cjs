// SSSF's obs relay, run in a remote VM for one pi call
// (docs/designs/remote-sandboxes.md, decisions 37 to 50).
//
// The pi-observability extension posts its events here instead of to the
// engineer's obs server, which the VM can't reach and whose token stays on
// the host. Each event goes to the file named in argv[2], one JSON line each,
// and to stdout, which the host reads live; the host checks them and posts
// them to obs. The first stdout line is {"sssf_obs_relay": <port>}.
//
// No dependencies: it runs with the Node the VM already has for pi.
"use strict";
const http = require("node:http");
const fs = require("node:fs");

const file = process.argv[2];
const MAX_REQUEST_BYTES = 4 * 1024 * 1024;   // obs's own limit

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
      process.stdout.write(text);
    }
    answer(res, 200, { ingested: events.length, rejected: [] });
  });
});

server.listen(0, "127.0.0.1", () => {
  process.stdout.write(JSON.stringify({ sssf_obs_relay: server.address().port }) + "\n");
});
