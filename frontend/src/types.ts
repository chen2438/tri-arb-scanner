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
  route_id: string;
  state: "active" | "closed";
  assets: [string, string, string, string];
  start_amount: string;
  final_amount: string;
  gross_return_bps: string;
  modeled_return_bps: string;
  safety_buffer_bps: string;
  net_return_bps: string;
  estimated_profit_usdt: string;
  confirmed_capacity_usdt: string;
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
  shard_id: number;
  state: string;
  generation: number;
  subscription_count: number;
  error: string | null;
};

export type ScannerStatus = {
  phase: string;
  ready: boolean;
  market_count: number;
  route_count: number;
  ticker_count: number;
  price_reference_count: number;
  depth_book_count: number;
  subscription_count: number;
  active_opportunity_count: number;
  rest_metadata_age_ms: number | null;
  rest_clock_age_ms: number | null;
  rest_ticker_age_ms: number | null;
  rest_price_reference_age_ms: number | null;
  last_scan_at: string | null;
  last_error: string | null;
  websocket_connections: WebSocketConnection[];
};

export type PublicConfig = {
  anchor_asset: string;
  notional: string;
  min_net_return_bps: string;
  safety_buffer_bps: string;
  depth_levels: number;
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
