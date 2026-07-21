import { expect, test } from "vitest";

import { buildHistoryQuery, SEQUENCE_GAP_CLOSE_CODE } from "./useScanner";

test("builds server-side history filters without leaking ALL sentinels", () => {
  const filtered = new URLSearchParams(
    buildHistoryQuery({ anchor: "USDC", exchange: "BYBIT" }, "next page"),
  );
  expect(Object.fromEntries(filtered)).toEqual({
    limit: "50",
    anchor: "USDC",
    exchange: "BYBIT",
    cursor: "next page",
  });

  const unfiltered = new URLSearchParams(
    buildHistoryQuery({ anchor: "ALL", exchange: "ALL" }),
  );
  expect(Object.fromEntries(unfiltered)).toEqual({ limit: "50" });
});

test("uses an application-defined WebSocket code for sequence-gap reconnects", () => {
  expect(SEQUENCE_GAP_CLOSE_CODE).toBeGreaterThanOrEqual(3000);
  expect(SEQUENCE_GAP_CLOSE_CODE).toBeLessThan(5000);
});
