import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { Opportunity } from "./types";

const opportunity: Opportunity = {
  id: "life-1", route_id: "USDT-BTC-ETH-USDT", state: "active",
  assets: ["USDT", "BTC", "ETH", "USDT"], start_amount: "100", final_amount: "100.32",
  gross_return_bps: "42", modeled_return_bps: "37", safety_buffer_bps: "5", net_return_bps: "32",
  estimated_profit_usdt: "0.32", confirmed_capacity_usdt: "275", first_seen_at: "2026-07-21T12:00:00.000Z",
  last_confirmed_at: "2026-07-21T12:00:01.000Z", closed_at: null, peak_net_return_bps: "35",
  close_reason: null, market_age_ms: 87, leg_skew_ms: 11, depth_confirmed: true,
  execution_warning: "预估结果，三腿无法原子成交，不保证实际利润",
  legs: [
    { symbol: "BTCUSDT", side: "BUY", from_asset: "USDT", to_asset: "BTC", input_amount: "100", output_amount: "0.0015", average_price: "66666.6", fee_rate: "0.001", fee_amount: "0.0000015", dust_amount: "0", levels_consumed: 2, book_version: "1", source_time: "2026-07-21T12:00:00.000Z", received_time: "2026-07-21T12:00:00.010Z", price_reference: "65000", price_protection_limit: "78000" },
    { symbol: "ETHBTC", side: "BUY", from_asset: "BTC", to_asset: "ETH", input_amount: "0.0015", output_amount: "0.03", average_price: "0.05", fee_rate: "0.001", fee_amount: "0.00003", dust_amount: "0", levels_consumed: 1, book_version: "2", source_time: "2026-07-21T12:00:00.003Z", received_time: "2026-07-21T12:00:00.012Z", price_reference: null, price_protection_limit: null },
    { symbol: "ETHUSDT", side: "SELL", from_asset: "ETH", to_asset: "USDT", input_amount: "0.03", output_amount: "100.32", average_price: "3344", fee_rate: "0.001", fee_amount: "0.10032", dust_amount: "0", levels_consumed: 1, book_version: "3", source_time: "2026-07-21T12:00:00.004Z", received_time: "2026-07-21T12:00:00.014Z", price_reference: null, price_protection_limit: null },
  ],
};

const scanner = {
  live: { connection: "connected" as const, sequence: 4, awaitingSnapshot: false, resyncRequired: false, opportunities: [opportunity], status: { phase: "scanning", ready: true, market_count: 2040, route_count: 736, ticker_count: 2040, price_reference_count: 6, depth_book_count: 6, subscription_count: 6, active_opportunity_count: 1, rest_metadata_age_ms: 1000, rest_clock_age_ms: 2000, rest_ticker_age_ms: 250, rest_price_reference_age_ms: 500, last_scan_at: "2026-07-21T12:00:01.000Z", last_error: null, websocket_connections: [{ shard_id: 0, state: "connected", generation: 2, subscription_count: 6, error: null }] } },
  config: { anchor_asset: "USDT", notional: "100", min_net_return_bps: "20", safety_buffer_bps: "5", depth_levels: 20 },
  history: [{ ...opportunity, id: "life-0", state: "closed" as const, closed_at: "2026-07-21T11:00:00.000Z", close_reason: "below_threshold" }],
  historyCursor: null, historyLoading: false, historyError: null, loadMoreHistory: vi.fn(), soundEnabled: false, toggleSound: vi.fn(),
};

vi.mock("./useScanner", () => ({ useScanner: () => scanner }));

beforeEach(() => vi.clearAllMocks());

test("shows and expands a depth-confirmed live opportunity", () => {
  render(<App />);
  expect(screen.getByText("+32 bps")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /套利路径/ }));
  expect(screen.getByText("20 档深度已确认 · 仍为预估")).toBeInTheDocument();
  expect(screen.getByText("第 3 腿")).toBeInTheDocument();
  expect(screen.getByText("买入保护上限")).toBeInTheDocument();
  expect(screen.getAllByText(/三腿非原子成交/)).toHaveLength(2);
});

test("navigates to history and system status", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "历史记录" }));
  expect(screen.getByText("跌破收益门槛")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "系统状态" }));
  expect(screen.getByText("公共 REST 行情")).toBeInTheDocument();
  expect(screen.getByText("分片 1")).toBeInTheDocument();
});

test("toggles the persisted sound preference", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "声音 关" }));
  expect(scanner.toggleSound).toHaveBeenCalledOnce();
});
