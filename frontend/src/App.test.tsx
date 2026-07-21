import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows the honest pre-market-data state", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

  render(<App />);

  expect(screen.getByText("行情管线尚未接入")).toBeInTheDocument();
  expect(screen.getByText("里程碑 1 / 6")).toBeInTheDocument();
  expect(await screen.findByText("本地服务在线")).toBeInTheDocument();
});
