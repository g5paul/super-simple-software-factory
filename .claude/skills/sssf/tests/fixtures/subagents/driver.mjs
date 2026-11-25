// Loads the shipped subagents.ts with a fake ExtensionAPI and calls one tool's execute.
// The real spawn("pi", ...) runs, with a stand-in pi first on PATH.
//
// DRIVER_TS      path of subagents.ts
// DRIVER_TOOL    tool name; DRIVER_ARGS its arguments, as JSON
// DRIVER_RESULT  file to write {result, sent, markerAtReturn} to
// DRIVER_MARKER  file the stand-in writes when it exits (checked at return)
// DRIVER_EXIT_EARLY  exit after this many ms without waiting for the tool
// DRIVER_GUARD   pi's output-guard.js; takeOverStdout() runs first
// DRIVER_FAIL    throw | callback | event: make the stdout write fail
import * as fs from "node:fs";
import { pathToFileURL } from "node:url";

if (process.env.DRIVER_GUARD) {
  (await import(pathToFileURL(process.env.DRIVER_GUARD).href)).takeOverStdout();
}
const fail = process.env.DRIVER_FAIL;
const proto = Object.getPrototypeOf(process.stdout);
if (fail === "throw") proto.write = () => { throw new Error("stdout closed"); };
if (fail === "callback") proto.write = (_line, cb) => { cb(new Error("EPIPE")); return false; };

const tools = {};
const sent = [];
const pi = {
  registerTool: (t) => { tools[t.name] = t; },
  registerCommand: () => {},
  on: () => {},
  sendMessage: (m) => { sent.push(m); },
  getThinkingLevel: () => "low",
};
const mod = await import(pathToFileURL(process.env.DRIVER_TS).href);
mod.default(pi);

const ctx = { model: { provider: "openai", id: "gpt" }, ui: { notify() {}, setWidget() {} } };
const call = tools[process.env.DRIVER_TOOL].execute("c1", JSON.parse(process.env.DRIVER_ARGS), undefined, undefined, ctx);
if (fail === "event") setTimeout(() => process.stdout.emit("error", new Error("EPIPE")), 300);
if (process.env.DRIVER_EXIT_EARLY) {
  setTimeout(() => process.exit(0), Number(process.env.DRIVER_EXIT_EARLY));
} else {
  const result = await call;
  const marker = process.env.DRIVER_MARKER;
  const markerAtReturn = !!marker && fs.existsSync(marker);
  const write = () => fs.writeFileSync(process.env.DRIVER_RESULT, JSON.stringify({
    result, sent, markerAtReturn,
    description: tools[process.env.DRIVER_TOOL].description,
  }));
  write();
  process.on("exit", write);   // a follow-up sent after the return still shows
}
