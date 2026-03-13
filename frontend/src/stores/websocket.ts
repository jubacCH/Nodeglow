import { create } from 'zustand';
import type { WsMessage, WsPingUpdate, WsAgentMetric } from '@/types';

interface WsState {
  isConnected: boolean;
  lastPingUpdates: Map<number, WsPingUpdate>;
  lastAgentMetrics: Map<number, WsAgentMetric>;
  connect: () => void;
  disconnect: () => void;
}

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let backoff = 1000;

export const useWsStore = create<WsState>((set, getState) => ({
  isConnected: false,
  lastPingUpdates: new Map(),
  lastAgentMetrics: new Map(),

  connect: () => {
    if (ws && ws.readyState <= WebSocket.OPEN) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/live`);

    ws.onopen = () => {
      set({ isConnected: true });
      backoff = 1000;
    };

    ws.onmessage = (e) => {
      try {
        const msg: WsMessage = JSON.parse(e.data);
        if (msg.type === 'ping_update') {
          const map = new Map(getState().lastPingUpdates);
          map.set(msg.host_id, msg);
          set({ lastPingUpdates: map });
        } else if (msg.type === 'agent_metric') {
          const map = new Map(getState().lastAgentMetrics);
          map.set(msg.agent_id, msg);
          set({ lastAgentMetrics: map });
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      set({ isConnected: false });
      ws = null;
      reconnectTimer = setTimeout(() => {
        backoff = Math.min(backoff * 2, 30000);
        getState().connect();
      }, backoff);
    };

    ws.onerror = () => ws?.close();
  },

  disconnect: () => {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    ws?.close();
    ws = null;
    set({ isConnected: false });
  },
}));
