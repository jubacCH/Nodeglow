'use client';

import { useSSE } from '@/hooks/useSSE';
import type { SyslogMessage } from '@/types';
import { useMemo } from 'react';
import Link from 'next/link';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { cn } from '@/lib/utils';
import { SEVERITY_LABELS, SeverityBadge, formatLogTime, hostHref } from './severity';

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

  const sevLabel = severity ? SEVERITY_LABELS[Number(severity)] : undefined;

  return (
    <Card as="section" padding="none" className="mb-4 overflow-hidden" aria-labelledby="live-tail-title">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h2 id="live-tail-title" className="text-body font-medium text-fg">Live tail</h2>
          {/* Connected = calm, colourless "Live"; connecting = hollow ring. */}
          <span role="status" className="inline-flex items-center gap-1.5 text-meta text-fg-2">
            {isStreaming ? (
              <span aria-hidden="true" className="h-[8px] w-[8px] rounded-full bg-fg-2" />
            ) : (
              <StatusDot status="unknown" label="" />
            )}
            {isStreaming ? 'Live' : 'Connecting…'}
          </span>
          <span className="num text-meta text-fg-3">
            {messages.length} message{messages.length !== 1 ? 's' : ''}
          </span>
          {sevLabel && (
            <span className="text-meta text-fg-3">Only {sevLabel.toLowerCase()} messages</span>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={clear} disabled={messages.length === 0}>
          Clear
        </Button>
      </div>

      <div className="max-h-80 overflow-y-auto" role="log" aria-live="off" aria-label="Live syslog messages">
        {messages.length === 0 ? (
          <p className="px-4 py-8 text-center text-ui text-fg-3">
            {isStreaming
              ? 'Connected. No new messages since the live tail started.'
              : 'Connecting to the live stream… Retrying every few seconds if the connection drops.'}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {messages.map((msg) => {
              const time = formatLogTime(msg.timestamp);
              return (
                <li
                  key={msg.__sseId}
                  className="flex min-w-0 items-start gap-3 px-4 py-1.5 hover:bg-hover max-[759px]:flex-wrap max-[759px]:gap-x-2 max-[759px]:gap-y-1"
                >
                  <time className="num shrink-0 whitespace-nowrap pt-0.5 font-mono text-meta text-fg-3" title={time.full}>
                    {time.short}
                  </time>
                  <SeverityBadge severity={msg.severity} short className="shrink-0" />
                  <Link
                    href={hostHref(msg.host_id, msg.hostname)}
                    className="shrink-0 whitespace-nowrap pt-0.5 font-mono text-meta text-fg-2 hover:text-accent"
                  >
                    {msg.hostname || '—'}
                  </Link>
                  <span className={cn('min-w-0 flex-1 truncate pt-0.5 font-mono text-meta text-fg', 'max-[759px]:basis-full max-[759px]:whitespace-normal max-[759px]:break-words')}>
                    {msg.message}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
