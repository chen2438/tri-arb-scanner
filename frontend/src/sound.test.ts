import { expect, test } from "vitest";

import { RouteSoundGate } from "./sound";

test("deduplicates the same route for exactly thirty seconds", () => {
  const gate = new RouteSoundGate();
  expect(gate.shouldPlay("route-a", 1_000)).toBe(true);
  expect(gate.shouldPlay("route-a", 30_999)).toBe(false);
  expect(gate.shouldPlay("route-a", 31_000)).toBe(true);
  expect(gate.shouldPlay("route-b", 31_000)).toBe(true);
});
