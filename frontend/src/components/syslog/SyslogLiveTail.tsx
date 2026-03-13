'use client';

import { useSSE } from '@/hooks/useSSE';
import type { SyslogMessage } from '@/types';
import { useMemo } from 'react';

const SEVERITY_COLORS: Record<number, string> = {
  0: 'bg-red-500 text-white',
  1: 'bg-red-400 text-white',
  2: 'bg-red-400/80 text-white',
  3: 'bg-orange-400 text-black',
  4: 'bg-amber-400 text-black',
  5: 'bg-blue-400 text-white',
  6: 'bg-sky-400/60 text-white',
  7: 'bg-slate-500 text-white',
};

const SEVERITY_LABELS: Record<number, string> = {
  0: 'EMERG',
  1: 'ALERT',
  2: 'CRIT',
  3: 'ERR',
  4: 'WARN',
  5: 'NOTICE',
  6: 'INFO',
  7: 'DEBUG',
};

interface SyslogLiveTailProps {
  enabled: boolean;
  severity?: string;
  host?: string;
  app?: string;
}

export function SyslogLiveTail({ enabled, severity, host, app }: SyslogLiveTailProps) {
  const streamUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (severity) params.set('severity', severity);
    if (host) params.set('host', host);
    if (app) params.set('app', app);
    const qs = params.toString();
    return `/syslog/stream${qs ? `?${qs}` : ''}`;
  }, [severity, host, app]);

  const { messages, isStreaming, clear } = useSSE<SyslogMessage>({
    url: streamUrl,
    enabled,
    maxMessages: 200,
  });

  if (!enabled) return null;

  return (
    <div className="mb-6 rounded-lg border border-white/[0.06] bg-white/[0.02] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/[0.06] bg-white/[0.02]">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            {isStreaming ? (
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
              </span>
            ) : (
              <span className="inline-flex rounded-full h-2.5 w-2.5 bg-slate-500" />
            )}
            <span className="text-xs font-medium text-slate-300">
              {isStreaming ? 'Live' : 'Connecting...'}
            </span>
          </div>
          <span className="text-xs text-slate-500">
            {messages.length} message{messages.length !== 1 ? 's' : ''}
          </span>
        </div>
        <button
          onClick={clear}
          className="px-2 py-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          Clear
        </button>
      </div>

      {/* Messages */}
      <div className="max-h-80 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-500">
            Waiting for messages...
          </div>
        ) : (
          <div className="divide-y divide-white/[0.03]">
            {messages.map((msg, i) => (
              <div
                key={`${msg.timestamp}-${i}`}
                className="px-4 py-2 hover:bg-white/[0.02] transition-colors flex items-start gap-3"
              >
                <span className="text-xs text-slate-500 font-mono whitespace-nowrap shrink-0 pt-0.5">
                  {new Date(msg.timestamp).toLocaleTimeString()}
                </span>
                <span
                  className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0 ${SEVERITY_COLORS[msg.severity] ?? 'bg-slate-500 text-white'}`}
                >
                  {SEVERITY_LABELS[msg.severity] ?? msg.severity}
                </span>
                <span className="text-xs text-sky-300/70 font-mono whitespace-nowrap shrink-0">
                  {msg.hostname}
                </span>
                <span className="text-xs text-slate-300 truncate min-w-0">
                  {msg.message}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
