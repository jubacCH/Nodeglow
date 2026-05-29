'use client';
import { useCallback, useEffect, useRef, useState } from 'react';

interface UseSSEOptions {
  url: string;
  enabled?: boolean;
  maxMessages?: number;
}

/** Stable monotonic id assigned at parse time, used for React list keys. */
export type SSEMessage<T> = T & { __sseId: number };

interface UseSSEReturn<T> {
  messages: SSEMessage<T>[];
  isStreaming: boolean;
  start: () => void;
  stop: () => void;
  clear: () => void;
}

export function useSSE<T = unknown>({
  url,
  enabled = false,
  maxMessages = 200,
}: UseSSEOptions): UseSSEReturn<T> {
  const [messages, setMessages] = useState<SSEMessage<T>[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Monotonic counter for stable list keys (array index is unstable because
  // messages are prepended). Survives reconnects within the hook lifetime.
  const seqRef = useRef(0);
  // Mirror `enabled` in a ref so the onerror reconnect reads the current value
  // instead of a stale closure captured at connect time.
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const cleanup = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const connect = useCallback(() => {
    cleanup();

    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      setIsStreaming(true);
    };

    es.onmessage = (event) => {
      // Ignore keepalive comments
      if (!event.data || event.data.trim() === '') return;

      try {
        const parsed = JSON.parse(event.data) as T;
        const withId = { ...parsed, __sseId: seqRef.current++ } as SSEMessage<T>;
        setMessages((prev) => {
          const next = [withId, ...prev];
          return next.length > maxMessages ? next.slice(0, maxMessages) : next;
        });
      } catch {
        // Ignore unparseable messages (keepalives, comments)
      }
    };

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      setIsStreaming(false);

      // Auto-reconnect after 2s, but only if still enabled at that point.
      reconnectTimerRef.current = setTimeout(() => {
        if (enabledRef.current) {
          connect();
        }
      }, 2000);
    };
  }, [url, maxMessages, cleanup]);

  const start = useCallback(() => {
    connect();
  }, [connect]);

  const stop = useCallback(() => {
    cleanup();
  }, [cleanup]);

  const clear = useCallback(() => {
    setMessages([]);
  }, []);

  useEffect(() => {
    if (enabled) {
      connect();
    } else {
      cleanup();
    }
    return cleanup;
  }, [enabled, connect, cleanup]);

  return { messages, isStreaming, start, stop, clear };
}
