import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { initialLiveState, liveReducer } from "./state";
import {
  playOpportunityTone,
  RouteSoundGate,
  SOUND_STORAGE_KEY,
} from "./sound";
import type { Opportunity, PublicConfig, SocketMessage } from "./types";

type HistoryResponse = { items: Opportunity[]; next_cursor: string | null };
type HistoryFilters = { anchor: string; exchange: string };
export const SEQUENCE_GAP_CLOSE_CODE = 4000;

export function buildHistoryQuery(
  filters: HistoryFilters,
  cursor?: string | null,
): string {
  const query = new URLSearchParams({ limit: "50" });
  if (filters.anchor !== "ALL") query.set("anchor", filters.anchor);
  if (filters.exchange !== "ALL") query.set("exchange", filters.exchange);
  if (cursor) query.set("cursor", cursor);
  return query.toString();
}

export function useScanner(historyFilters: HistoryFilters) {
  const [live, dispatch] = useReducer(liveReducer, initialLiveState);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [history, setHistory] = useState<Opportunity[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [soundEnabled, setSoundEnabled] = useState(
    () => {
      try {
        return localStorage.getItem(SOUND_STORAGE_KEY) === "true";
      } catch {
        return false;
      }
    },
  );
  const soundEnabledRef = useRef(soundEnabled);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const reconnectAttempt = useRef(0);
  const stopped = useRef(false);
  const knownLifecycleIds = useRef(new Set<string>());
  const expectedSequence = useRef<number | null>(null);
  const soundGate = useRef(new RouteSoundGate());
  const historyRequestId = useRef(0);

  const loadHistory = useCallback(async (cursor?: string | null) => {
    const requestId = ++historyRequestId.current;
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const query = buildHistoryQuery(historyFilters, cursor);
      const response = await fetch(`/api/history?${query}`);
      if (!response.ok) throw new Error(`history request failed: ${response.status}`);
      const payload = (await response.json()) as HistoryResponse;
      if (requestId !== historyRequestId.current) return;
      setHistory((current) => (cursor ? [...current, ...payload.items] : payload.items));
      setHistoryCursor(payload.next_cursor);
    } catch {
      if (requestId === historyRequestId.current) {
        setHistoryError("历史记录暂时无法加载，请稍后重试。");
      }
    } finally {
      if (requestId === historyRequestId.current) setHistoryLoading(false);
    }
  }, [historyFilters.anchor, historyFilters.exchange]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/config", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`config request failed: ${response.status}`);
        return response.json();
      })
      .then((configuration) => setConfig(configuration as PublicConfig))
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          dispatch({ type: "socket.closed" });
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    void loadHistory(null);
  }, [loadHistory]);

  useEffect(() => {
    stopped.current = false;

    const connect = () => {
      if (stopped.current) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws/opportunities`);
      socketRef.current = socket;
      expectedSequence.current = null;
      dispatch({ type: "socket.open" });

      socket.addEventListener("open", () => {
        reconnectAttempt.current = 0;
      });
      socket.addEventListener("message", (event) => {
        let message: SocketMessage;
        try {
          message = JSON.parse(String(event.data)) as SocketMessage;
        } catch {
          socket.close(1002, "invalid JSON");
          return;
        }
        if (message.type === "snapshot") {
          expectedSequence.current = message.sequence;
          const snapshot = message.data as { opportunities: Opportunity[] };
          knownLifecycleIds.current = new Set(snapshot.opportunities.map((item) => item.id));
        } else if (expectedSequence.current === null || message.sequence !== expectedSequence.current + 1) {
          dispatch({ type: "message", message });
          return;
        } else if (message.type === "opportunity.upsert") {
          const opportunity = message.data as Opportunity;
          if (!knownLifecycleIds.current.has(opportunity.id)) {
            knownLifecycleIds.current.add(opportunity.id);
            if (soundEnabledRef.current && soundGate.current.shouldPlay(opportunity.route_id, Date.now())) {
              playOpportunityTone();
            }
          }
        } else if (message.type === "opportunity.closed") {
          const opportunity = message.data as Opportunity;
          knownLifecycleIds.current.delete(opportunity.id);
          void loadHistory(null);
        }
        expectedSequence.current = message.sequence;
        dispatch({ type: "message", message });
      });
      socket.addEventListener("close", () => {
        if (socketRef.current === socket) socketRef.current = null;
        dispatch({ type: "socket.closed" });
        if (stopped.current) return;
        const delay = Math.min(30_000, 1_000 * 2 ** reconnectAttempt.current);
        reconnectAttempt.current += 1;
        reconnectTimer.current = window.setTimeout(connect, delay);
      });
      socket.addEventListener("error", () => socket.close());
    };

    connect();
    return () => {
      stopped.current = true;
      if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current);
      socketRef.current?.close(1000, "page unmounted");
    };
  }, [loadHistory]);

  useEffect(() => {
    if (live.resyncRequired) {
      socketRef.current?.close(SEQUENCE_GAP_CLOSE_CODE, "sequence gap");
    }
  }, [live.resyncRequired]);

  const toggleSound = () => {
    setSoundEnabled((current) => {
      const next = !current;
      soundEnabledRef.current = next;
      try {
        localStorage.setItem(SOUND_STORAGE_KEY, String(next));
      } catch {
        // Private browsing may disable storage; the in-memory setting still works.
      }
      if (next) playOpportunityTone();
      return next;
    });
  };

  return {
    live,
    config,
    history,
    historyCursor,
    historyLoading,
    historyError,
    loadMoreHistory: () => loadHistory(historyCursor),
    soundEnabled,
    toggleSound,
  };
}
