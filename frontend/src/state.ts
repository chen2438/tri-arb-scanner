import type { Opportunity, ScannerStatus, SocketMessage } from "./types";

export type ConnectionState = "connecting" | "connected" | "resyncing" | "offline";

export type LiveState = {
  connection: ConnectionState;
  sequence: number | null;
  awaitingSnapshot: boolean;
  resyncRequired: boolean;
  opportunities: Opportunity[];
  status: ScannerStatus | null;
};

export type LiveAction =
  | { type: "socket.open" }
  | { type: "socket.closed" }
  | { type: "message"; message: SocketMessage };

export const initialLiveState: LiveState = {
  connection: "connecting",
  sequence: null,
  awaitingSnapshot: true,
  resyncRequired: false,
  opportunities: [],
  status: null,
};

function sortOpportunities(values: Opportunity[]): Opportunity[] {
  return [...values].sort((left, right) => {
    const byReturn = compareDecimalStrings(right.net_return_bps, left.net_return_bps);
    if (byReturn !== 0) return byReturn;
    const byTime = right.last_confirmed_at.localeCompare(left.last_confirmed_at);
    return byTime !== 0 ? byTime : left.route_id.localeCompare(right.route_id);
  });
}

function compareDecimalStrings(left: string, right: string): number {
  const normalize = (value: string) => {
    const negative = value.startsWith("-");
    const unsigned = negative || value.startsWith("+") ? value.slice(1) : value;
    const [integer = "0", fraction = ""] = unsigned.split(".");
    return {
      negative,
      integer: integer.replace(/^0+(?=\d)/, ""),
      fraction: fraction.replace(/0+$/, ""),
    };
  };
  const a = normalize(left);
  const b = normalize(right);
  if (a.negative !== b.negative) return a.negative ? -1 : 1;
  const direction = a.negative ? -1 : 1;
  if (a.integer.length !== b.integer.length) {
    return (a.integer.length > b.integer.length ? 1 : -1) * direction;
  }
  const integerOrder = a.integer.localeCompare(b.integer);
  if (integerOrder !== 0) return integerOrder * direction;
  const width = Math.max(a.fraction.length, b.fraction.length);
  const fractionOrder = a.fraction.padEnd(width, "0").localeCompare(b.fraction.padEnd(width, "0"));
  return fractionOrder * direction;
}

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
  if (action.type === "socket.open") {
    return {
      ...state,
      connection: "connecting",
      sequence: null,
      awaitingSnapshot: true,
      resyncRequired: false,
    };
  }
  if (action.type === "socket.closed") {
    return { ...state, connection: "offline", awaitingSnapshot: true };
  }

  const message = action.message;
  if (message.type === "snapshot") {
    const data = message.data as { opportunities: Opportunity[]; status: ScannerStatus };
    return {
      connection: "connected",
      sequence: message.sequence,
      awaitingSnapshot: false,
      resyncRequired: false,
      opportunities: sortOpportunities(data.opportunities),
      status: data.status,
    };
  }
  if (state.awaitingSnapshot) return state;
  if (state.sequence === null || message.sequence !== state.sequence + 1) {
    return {
      ...state,
      connection: "resyncing",
      awaitingSnapshot: true,
      resyncRequired: true,
    };
  }

  if (message.type === "opportunity.upsert") {
    const opportunity = message.data as Opportunity;
    const opportunities = state.opportunities.filter((item) => item.id !== opportunity.id);
    opportunities.push(opportunity);
    return {
      ...state,
      sequence: message.sequence,
      opportunities: sortOpportunities(opportunities),
    };
  }
  if (message.type === "opportunity.closed") {
    const opportunity = message.data as Opportunity;
    return {
      ...state,
      sequence: message.sequence,
      opportunities: state.opportunities.filter((item) => item.id !== opportunity.id),
    };
  }
  if (message.type === "status.changed") {
    return {
      ...state,
      sequence: message.sequence,
      status: message.data as ScannerStatus,
    };
  }
  return { ...state, sequence: message.sequence };
}
