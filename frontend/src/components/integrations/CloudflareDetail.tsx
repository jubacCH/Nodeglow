'use client';

import { useState } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { Globe, Shield, HardDrive, ArrowUpDown, Lock, ChevronDown, ChevronRight } from 'lucide-react';

interface DnsRecord {
  type: string;
  name: string;
  content: string;
  proxied: boolean;
  ttl: number;
}

interface FirewallEvent {
  action: string;
  source: string;
  country: string;
  rule: string;
  host: string;
  uri: string;
  timestamp: string;
}

interface ZoneAnalytics {
  requests_all: number;
  requests_cached: number;
  cache_pct: number;
  bandwidth_all: number;
  bandwidth_cached: number;
  threats: number;
}

interface Zone {
  id: string;
  name: string;
  status: string;
  plan: string;
  ssl_mode: string;
  dns_records: DnsRecord[];
  dns_count: number;
  analytics: ZoneAnalytics;
  firewall_events: FirewallEvent[];
}

interface CloudflareData {
  zone_count: number;
  zones: Zone[];
  totals: {
    requests: number;
    bandwidth: number;
    threats: number;
    cache_pct: number;
  };
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function StatCard({ label, value, icon: Icon, color }: {
  label: string; value: string | number;
  icon: React.ElementType; color: string;
}) {
  return (
    <GlassCard className="p-4 text-center">
      <div className={`inline-flex p-2 rounded-lg bg-${color}-500/10 mb-2`}>
        <Icon size={18} className={`text-${color}-400`} />
      </div>
      <p className="text-2xl font-bold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

function ZoneCard({ zone }: { zone: Zone }) {
  const [expanded, setExpanded] = useState(false);
  const [showDns, setShowDns] = useState(false);

  return (
    <GlassCard className="p-4">
      <div
        className="flex items-center gap-3 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <StatusDot status={zone.status === 'active' ? 'online' : 'maintenance'} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-200">{zone.name}</span>
            <Badge>{zone.plan}</Badge>
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-xs text-slate-500">
              {formatNumber(zone.analytics.requests_all)} requests
            </span>
            <span className="text-xs text-slate-500">
              {zone.analytics.cache_pct}% cached
            </span>
            {zone.analytics.threats > 0 && (
              <span className="text-xs text-amber-400">
                {zone.analytics.threats} threats
              </span>
            )}
            <span className="text-xs text-slate-500">
              SSL: {zone.ssl_mode}
            </span>
          </div>
        </div>
        <span className="text-xs text-slate-500">{zone.dns_count} records</span>
        {expanded ? <ChevronDown size={14} className="text-slate-500" /> : <ChevronRight size={14} className="text-slate-500" />}
      </div>

      {expanded && (
        <div className="mt-4 space-y-4">
          {/* Analytics */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="text-center p-3 rounded-lg bg-white/[0.02]">
              <p className="text-lg font-bold text-sky-400">{formatNumber(zone.analytics.requests_all)}</p>
              <p className="text-[10px] text-slate-500 uppercase">Requests 24h</p>
            </div>
            <div className="text-center p-3 rounded-lg bg-white/[0.02]">
              <p className="text-lg font-bold text-emerald-400">{zone.analytics.cache_pct}%</p>
              <p className="text-[10px] text-slate-500 uppercase">Cache Hit</p>
            </div>
            <div className="text-center p-3 rounded-lg bg-white/[0.02]">
              <p className="text-lg font-bold text-violet-400">{formatBytes(zone.analytics.bandwidth_all)}</p>
              <p className="text-[10px] text-slate-500 uppercase">Bandwidth</p>
            </div>
            <div className="text-center p-3 rounded-lg bg-white/[0.02]">
              <p className={`text-lg font-bold ${zone.analytics.threats > 0 ? 'text-amber-400' : 'text-slate-400'}`}>
                {formatNumber(zone.analytics.threats)}
              </p>
              <p className="text-[10px] text-slate-500 uppercase">Threats</p>
            </div>
          </div>

          {/* DNS Records Toggle */}
          <div>
            <button
              onClick={(e) => { e.stopPropagation(); setShowDns(!showDns); }}
              className="text-xs text-slate-400 hover:text-slate-200 transition-colors flex items-center gap-1"
            >
              {showDns ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              DNS Records ({zone.dns_count})
            </button>
            {showDns && (
              <div className="mt-2 max-h-[300px] overflow-y-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-white/[0.06]">
                      <th className="text-left px-2 py-1.5 text-slate-500 font-medium">Type</th>
                      <th className="text-left px-2 py-1.5 text-slate-500 font-medium">Name</th>
                      <th className="text-left px-2 py-1.5 text-slate-500 font-medium">Content</th>
                      <th className="text-left px-2 py-1.5 text-slate-500 font-medium">Proxy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {zone.dns_records.map((r, i) => (
                      <tr key={i} className="border-b border-white/[0.04]">
                        <td className="px-2 py-1.5">
                          <Badge>{r.type}</Badge>
                        </td>
                        <td className="px-2 py-1.5 text-slate-300 font-mono truncate max-w-[200px]">
                          {r.name}
                        </td>
                        <td className="px-2 py-1.5 text-slate-400 font-mono truncate max-w-[200px]">
                          {r.content}
                        </td>
                        <td className="px-2 py-1.5">
                          {r.proxied ? (
                            <span className="text-orange-400 text-[10px] font-medium">Proxied</span>
                          ) : (
                            <span className="text-slate-500 text-[10px]">DNS only</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Firewall Events */}
          {zone.firewall_events.length > 0 && (
            <div>
              <p className="text-xs text-slate-400 mb-2 flex items-center gap-1">
                <Shield size={12} />
                Recent Firewall Events
              </p>
              <div className="space-y-1 max-h-[200px] overflow-y-auto">
                {zone.firewall_events.map((ev, i) => (
                  <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded bg-white/[0.02] text-xs">
                    <Badge variant={ev.action === 'block' ? 'severity' : undefined}
                           severity={ev.action === 'block' ? 'critical' : undefined}>
                      {ev.action}
                    </Badge>
                    <span className="text-slate-400 font-mono">{ev.source}</span>
                    {ev.country && <span className="text-slate-500">{ev.country}</span>}
                    <span className="flex-1 text-slate-400 truncate">{ev.uri}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </GlassCard>
  );
}

export function CloudflareDetail({ data }: { data: CloudflareData }) {
  if (!data) return null;

  return (
    <div className="space-y-6">
      {/* Overview Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard icon={Globe} color="orange" label="Zones" value={data.zone_count} />
        <StatCard icon={ArrowUpDown} color="sky" label="Requests 24h" value={formatNumber(data.totals.requests)} />
        <StatCard icon={HardDrive} color="violet" label="Bandwidth 24h" value={formatBytes(data.totals.bandwidth)} />
        <StatCard icon={Shield} color="amber" label="Threats 24h" value={formatNumber(data.totals.threats)} />
      </div>

      {/* Cache & SSL Summary */}
      <GlassCard className="p-4">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <Lock size={14} className="text-emerald-400" />
            <span className="text-sm text-slate-300">Global Cache Hit Rate:</span>
            <span className="text-sm font-bold text-emerald-400">{data.totals.cache_pct}%</span>
          </div>
        </div>
      </GlassCard>

      {/* Zones */}
      <div>
        <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
          <Globe size={14} className="text-orange-400" />
          Zones ({data.zone_count})
        </h3>
        <div className="space-y-3">
          {data.zones.map((zone) => (
            <ZoneCard key={zone.id} zone={zone} />
          ))}
        </div>
      </div>
    </div>
  );
}
