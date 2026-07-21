import { expect, test } from "vitest";

import { initialLiveState, liveReducer } from "./state";
import type { Opportunity, ScannerStatus } from "./types";

const status = { ready: true } as ScannerStatus;
const base = { id: "one", route_id: "route-a", net_return_bps: "9.9", last_confirmed_at: "2026-01-01T00:00:00Z" } as Opportunity;

test("snapshot replaces local state and orders decimal strings without float arithmetic", () => {
  const highPrecision = { ...base, id: "two", route_id: "route-b", net_return_bps: "9.9000000000000000001" };
  const state = liveReducer(initialLiveState, { type: "message", message: { type: "snapshot", sequence: 10, data: { opportunities: [base, highPrecision], status } } });
  expect(state.opportunities.map((item) => item.id)).toEqual(["two", "one"]);
  expect(state.connection).toBe("connected");
});

test("sequence gap discards the increment until a fresh snapshot arrives", () => {
  const current = liveReducer(initialLiveState, { type: "message", message: { type: "snapshot", sequence: 10, data: { opportunities: [base], status } } });
  const gap = liveReducer(current, { type: "message", message: { type: "opportunity.closed", sequence: 12, data: base } });
  expect(gap.resyncRequired).toBe(true);
  expect(gap.opportunities).toEqual([base]);
  const recovered = liveReducer(gap, { type: "message", message: { type: "snapshot", sequence: 20, data: { opportunities: [], status } } });
  expect(recovered.resyncRequired).toBe(false);
  expect(recovered.opportunities).toEqual([]);
});

test("upsert and close apply only to consecutive messages", () => {
  const snapshot = liveReducer(initialLiveState, { type: "message", message: { type: "snapshot", sequence: 2, data: { opportunities: [], status } } });
  const opened = liveReducer(snapshot, { type: "message", message: { type: "opportunity.upsert", sequence: 3, data: base } });
  expect(opened.opportunities).toEqual([base]);
  const closed = liveReducer(opened, { type: "message", message: { type: "opportunity.closed", sequence: 4, data: base } });
  expect(closed.opportunities).toEqual([]);
});
