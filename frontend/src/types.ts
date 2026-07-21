export type Leg = {
  symbol: string;
  side: "BUY" | "SELL";
  from_asset: string;
  to_asset: string;
  input_amount: string;
  output_amount: string;
  average_price: string;
  fee_rate: string;
  fee_amount: string;
  dust_amount: string;
  levels_consumed: number;
  book_version: string;
  source_time: string;
  received_time: string;
  price_reference: string | null;
  price_protection_limit: string | null;
};

export type Opportunity = {
  id: string;
  exchange: string;
  route_id: string;
  state: "active" | "closed";
  assets: [string, string, string, string];
  anchor_asset: string;
  start_amount: string;
  final_amount: string;
  gross_return_bps: string;
  modeled_return_bps: string;
  safety_buffer_bps: string;
  net_return_bps: string;
  estimated_profit_usdt: string;
  estimated_profit: string;
  confirmed_capacity_usdt: string;
  confirmed_capacity: string;
  first_seen_at: string;
  last_confirmed_at: string;
  closed_at: string | null;
  peak_net_return_bps: string;
  close_reason: string | null;
  market_age_ms: number;
  leg_skew_ms: number;
  depth_confirmed: true;
  execution_warning: string;
  legs: [Leg, Leg, Leg];
};

export type WebSocketConnection = {
  exchange: string;
  shard_id: number;
  state: string;
  generation: number;
  subscription_count: number;
  error: string | null;
};

export type ExchangeStatus = {
  exchange: string;
  phase: string;
  ready: boolean;
  market_count: number;
  route_count: number;
  ticker_count: number;
  market_activity_count: number;
  core_market_count: number;
  core_route_count: number;
  depth_book_count: number;
  subscription_count: number;
  rest_metadata_age_ms: number | null;
  rest_clock_age_ms: number | null;
  rest_ticker_age_ms: number | null;
  rest_price_reference_age_ms: number | null;
  rest_market_activity_age_ms: number | null;
  last_error: string | null;
  websocket_connections: WebSocketConnection[];
};

export type ScannerStatus = {
  phase: string;
  ready: boolean;
  exchanges: ExchangeStatus[];
  market_count: number;
  route_count: number;
  ticker_count: number;
  price_reference_count: number;
  market_activity_count: number;
  core_market_count: number;
  core_route_count: number;
  depth_book_count: number;
  subscription_count: number;
  active_opportunity_count: number;
  diagnostics: ScannerDiagnostics | null;
  rest_metadata_age_ms: number | null;
  rest_clock_age_ms: number | null;
  rest_ticker_age_ms: number | null;
  rest_price_reference_age_ms: number | null;
  rest_market_activity_age_ms: number | null;
  last_scan_at: string | null;
  last_error: string | null;
  websocket_connections: WebSocketConnection[];
};

export type NearMiss = {
  exchange: string;
  route_id: string;
  assets: [string, string, string, string];
  net_return_bps: string;
  estimated_profit: string;
  confirmed_capacity: string;
  market_age_ms: number;
  leg_skew_ms: number;
};

export type ScannerDiagnostics = {
  updated_at_ms: number;
  total_route_count: number;
  priced_route_count: number;
  positive_route_count: number;
  shortlisted_route_count: number;
  depth_confirmed_count: number;
  best_estimated_return_bps: string | null;
  rejection_counts: Record<string, number>;
  near_misses: NearMiss[];
  rolling_confirmed_sample_count: number;
  rolling_max_net_return_bps: string | null;
  rolling_buckets: Record<string, number>;
};

export type PublicConfig = {
  anchor_asset: string;
  anchor_assets: string[];
  notional: string;
  min_net_return_bps: string;
  safety_buffer_bps: string;
  depth_levels: number;
  okx_enabled: boolean;
  okx_taker_commission: string;
};

export type SocketMessage = {
  type:
    | "snapshot"
    | "opportunity.upsert"
    | "opportunity.closed"
    | "status.changed"
    | "heartbeat";
  sequence: number;
  data: unknown;
};
