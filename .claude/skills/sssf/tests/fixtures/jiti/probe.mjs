// Loads one extension through jiti the way pi's loader does, and prints what its default export returns.
// usage: node probe.mjs <jiti package folder> <extension path>
import { createRequire } from "node:module";
import path from "node:path";

const [jitiDir, extension] = process.argv.slice(2);
const req = createRequire(path.join(jitiDir, "package.json"));
const { createJiti } = req(jitiDir);
const jiti = createJiti(import.meta.url, { moduleCache: false, alias: {} });
const fn = await jiti.import(extension, { default: true });
process.stdout.write(String(fn()));
