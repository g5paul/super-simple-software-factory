import { expect, test } from "bun:test";
import { ttlCache } from "./ttl.ts";

test("a result is reused until it is older than the ttl", () => {
  let now = 1_000;
  let calls = 0;
  const cached = ttlCache<number>(30_000, () => now);
  const read = () => cached("5h", () => ++calls);
  expect(read()).toBe(1);
  now += 29_999;
  expect(read()).toBe(1);
  now += 1;
  expect(read()).toBe(2);
  expect(calls).toBe(2);
});

test("each key keeps its own result", () => {
  const cached = ttlCache<string>(30_000, () => 0);
  expect(cached("5h", () => "five hours")).toBe("five hours");
  expect(cached("7d", () => "seven days")).toBe("seven days");
  expect(cached("5h", () => "again")).toBe("five hours");
});

test("a read that throws caches nothing", () => {
  const cached = ttlCache<number>(30_000, () => 0);
  expect(() => cached("5h", () => { throw new Error("trace would not open"); })).toThrow("trace would not open");
  expect(cached("5h", () => 7)).toBe(7);
});
