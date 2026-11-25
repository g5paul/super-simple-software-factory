// Calls enableCompileCache() the way pi's bin does, loads a sibling module so there is something
// to cache, and prints the result and Node's status names as JSON.
import { enableCompileCache, constants } from "node:module";

const result = enableCompileCache();
const mod = await import("./mod.mjs");
console.log(JSON.stringify({ result, status: constants.compileCacheStatus, value: mod.value }));
