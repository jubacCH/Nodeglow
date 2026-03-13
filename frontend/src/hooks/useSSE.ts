'use client';
import { useCallback, useEffect, useRef, useState } from 'react';

interface UseSSEOptions {
  url: string;
  enabled?: boolean;
  maxMessages?: number;
}

interface UseSSEReturn<T> {
  messages: T[];
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
  const [messages, setMessages] = useState<T[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
        setMessages((prev) => {
          const next = [parsed, ...prev];
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

      // Auto-reconnect after 2s
      reconnectTimerRef.current = setTimeout(() => {
        if (enabled) {
          connect();
        }
      }, 2000);
    };
  }, [url, maxMessages, enabled, cleanup]);

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
