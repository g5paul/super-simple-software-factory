// Loaded with `node --import register.mjs driver.mjs`: sends the pi packages and
// themeMap.ts, which the shipped subagents.ts imports, to stubs.mjs.
import { register } from "node:module";

register("./hooks.mjs", import.meta.url);
