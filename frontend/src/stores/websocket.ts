import { create } from 'zustand';
import type { QueryClient } from '@tanstack/react-query';
import type { WsMessage, WsPingUpdate, WsAgentMetric } from '@/types';
import { applyLiveUpdates, liveRefetchInterval } from '@/lib/liveUpdates';

interface WsState {
  isConnected: boolean;
  /** Open the socket. Live events are folded into `queryClient`'s cache. */
  connect: (queryClient: QueryClient) => void;
  disconnect: () => void;
}

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let backoff = 1000;
// Set while an intentional disconnect() is in flight so the onclose handler
// does not schedule a reconnect (e.g. on unmount/logout).
let intentionalDisconnect = false;

// Events are buffered (latest per host/agent wins) and flushed once per
// animation frame, so a burst of N pings costs one cache pass instead of N.
let client: QueryClient | null = null;
let pendingPings = new Map<number, WsPingUpdate>();
let pendingAgents = new Map<number, WsAgentMetric>();
let flushScheduled = false;

function flush() {
  flushScheduled = false;
  const pings = pendingPings;
  const agents = pendingAgents;
  pendingPings = new Map();
  pendingAgents = new Map();
  if (client) applyLiveUpdates(client, pings, agents);
}

function scheduleFlush() {
  if (flushScheduled) return;
  flushScheduled = true;
  // rAF is paused in background tabs; the buffer is keyed by id so it stays
  // bounded and is applied as soon as the tab is visible again.
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(flush);
  else setTimeout(flush, 16);
}

export const useWsStore = create<WsState>((set, getState) => ({
  isConnected: false,

  connect: (queryClient) => {
    client = queryClient;
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;

    // A fresh connect cancels any pending intentional-disconnect state.
    intentionalDisconnect = false;

    // Clear any pending reconnect timer to avoid duplicate connections
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const sock = new WebSocket(`${proto}//${location.host}/ws/live`);
    ws = sock;

    sock.onopen = () => {
      set({ isConnected: true });
      backoff = 1000;
    };

    sock.onmessage = (e) => {
      try {
        const msg: WsMessage = JSON.parse(e.data);
        if (msg.type === 'ping_update') {
          pendingPings.set(msg.host_id, msg);
          scheduleFlush();
        } else if (msg.type === 'agent_metric') {
          pendingAgents.set(msg.agent_id, msg);
          scheduleFlush();
        }
      } catch {
        // ignore malformed messages
      }
    };

    sock.onclose = () => {
      // A socket replaced by a newer connect() must not clobber its state.
      if (ws !== sock && ws !== null) return;
      set({ isConnected: false });
      ws = null;
      // Do not reconnect if the socket was closed by an intentional disconnect.
      if (intentionalDisconnect) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        backoff = Math.min(backoff * 2, 30000);
        if (client) getState().connect(client);
      }, backoff);
    };

    sock.onerror = () => sock.close();
  },

  disconnect: () => {
    // Mark the disconnect as intentional so onclose does not reconnect, and
    // clear the pending reconnect timer to fully stop the reconnect loop.
    intentionalDisconnect = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    ws?.close();
    ws = null;
    pendingPings.clear();
    pendingAgents.clear();
    set({ isConnected: false });
  },
}));

/**
 * refetchInterval for queries the WebSocket keeps live: `fast` while the
 * socket is down, `slow` while it is connected.
 */
export function whileLive(fast: number, slow: number) {
  return liveRefetchInterval(() => useWsStore.getState().isConnected, fast, slow);
}
