import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { Opportunity } from "./types";

const opportunity: Opportunity = {
  id: "life-1", route_id: "USDT-BTC-ETH-USDT", state: "active",
  assets: ["USDT", "BTC", "ETH", "USDT"], anchor_asset: "USDT", start_amount: "100", final_amount: "100.32",
  gross_return_bps: "42", modeled_return_bps: "37", safety_buffer_bps: "5", net_return_bps: "32",
  estimated_profit_usdt: "0.32", estimated_profit: "0.32", confirmed_capacity_usdt: "275", confirmed_capacity: "275", first_seen_at: "2026-07-21T12:00:00.000Z",
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
  live: { connection: "connected" as const, sequence: 4, awaitingSnapshot: false, resyncRequired: false, opportunities: [opportunity], status: { phase: "scanning", ready: true, market_count: 2040, route_count: 736, ticker_count: 2040, price_reference_count: 6, depth_book_count: 6, subscription_count: 6, active_opportunity_count: 1, diagnostics: { updated_at_ms: 1000, total_route_count: 736, priced_route_count: 730, positive_route_count: 3, shortlisted_route_count: 20, depth_confirmed_count: 1, best_estimated_return_bps: "18", rejection_counts: { price_protection: 2, missing_current_depth: 17 }, near_misses: [{ route_id: "near", assets: ["USDT", "A", "B", "USDT"], net_return_bps: "15", estimated_profit: "0.15", confirmed_capacity: "250", market_age_ms: 80, leg_skew_ms: 10 }], rolling_confirmed_sample_count: 12, rolling_max_net_return_bps: "19", rolling_buckets: { negative: 4, "0_to_5": 2, "5_to_10": 2, "10_to_threshold": 4, opportunity: 0 } }, rest_metadata_age_ms: 1000, rest_clock_age_ms: 2000, rest_ticker_age_ms: 250, rest_price_reference_age_ms: 500, last_scan_at: "2026-07-21T12:00:01.000Z", last_error: null, websocket_connections: [{ shard_id: 0, state: "connected", generation: 2, subscription_count: 6, error: null }] } },
  config: { anchor_asset: "USDT", anchor_assets: ["USDT", "USDC", "USD1"], notional: "100", min_net_return_bps: "20", safety_buffer_bps: "5", depth_levels: 20 },
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

test("shows the current scan funnel, rejection reasons, and near misses", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "扫描诊断" }));
  expect(screen.getByText("本轮机会漏斗")).toBeInTheDocument();
  expect(screen.getByText("触发交易所价格保护")).toBeInTheDocument();
  expect(screen.getByText("USDT → A → B → USDT")).toBeInTheDocument();
  expect(screen.getByText("+15 bps")).toBeInTheDocument();
});

test("filters opportunities by anchor asset", () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("锚定资产"), { target: { value: "USDC" } });
  expect(screen.getByText("正在等待符合门槛的机会")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("锚定资产"), { target: { value: "USDT" } });
  expect(screen.getByText("+32 bps")).toBeInTheDocument();
});

test("toggles the persisted sound preference", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "声音 关" }));
  expect(scanner.toggleSound).toHaveBeenCalledOnce();
});
