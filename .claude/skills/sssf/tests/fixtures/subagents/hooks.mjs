const STUBS = new URL("./stubs.mjs", import.meta.url).href;

export async function resolve(specifier, context, next) {
  if (specifier.startsWith("@mariozechner/") || specifier.startsWith("@sinclair/") || specifier.endsWith("themeMap.ts")) {
    return { url: STUBS, shortCircuit: true };
  }
  return next(specifier, context);
}
