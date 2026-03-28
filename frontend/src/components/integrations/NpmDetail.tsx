'use client';

import { useState } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import {
  Globe, Lock, ArrowRight, Radio, Skull,
  ChevronDown, ChevronRight,
} from 'lucide-react';

interface ProxyHost {
  id: number;
  domains: string[];
  domain_primary: string;
  enabled: boolean;
  ssl_forced: boolean;
  certificate_id: number;
  forward: string;
  has_access_list: boolean;
  advanced_config: boolean;
}

interface Certificate {
  id: number;
  nice_name: string;
  provider: string;
  domains: string[];
  expires_on: string | null;
  days_left: number | null;
}

interface Redirection {
  id: number;
  domains: string[];
  forward_url: string;
  forward_scheme: string;
  forward_code: number;
  enabled: boolean;
  preserve_path: boolean;
}

interface Stream {
  id: number;
  incoming_port: number;
  forwarding_host: string;
  forwarding_port: number;
  enabled: boolean;
  tcp: boolean;
  udp: boolean;
}

interface DeadHost {
  id: number;
  domains: string[];
  enabled: boolean;
}

interface NpmData {
  proxy_hosts: ProxyHost[];
  proxy_count: number;
  online_count: number;
  offline_count: number;
  ssl_host_count: number;
  certificates: Certificate[];
  cert_count: number;
  certs_expiring_soon: number;
  certs_expired: number;
  redirections: Redirection[];
  redir_count: number;
  streams: Stream[];
  stream_count: number;
  dead_hosts: DeadHost[];
  dead_count: number;
}

function StatCard({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className={`text-2xl font-bold ${color || 'text-slate-100'}`}>{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

function certColor(days: number | null): string {
  if (days === null) return 'text-slate-500';
  if (days <= 0) return 'text-red-400';
  if (days <= 7) return 'text-red-400';
  if (days <= 30) return 'text-amber-400';
  return 'text-emerald-400';
}

function certDotStatus(days: number | null): 'online' | 'offline' | 'maintenance' | 'unknown' {
  if (days === null) return 'unknown';
  if (days <= 0) return 'offline';
  if (days <= 7) return 'offline';
  if (days <= 30) return 'maintenance';
  return 'online';
}

function Section({ title, icon: Icon, iconColor, count, children }: {
  title: string; icon: React.ElementType; iconColor: string;
  count?: number; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 mb-3 text-sm font-semibold text-slate-300 hover:text-slate-100 transition-colors"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Icon size={14} className={iconColor} />
        {title}
        {count !== undefined && (
          <span className="text-xs text-slate-500 font-normal ml-1">({count})</span>
        )}
      </button>
      {open && children}
    </div>
  );
}

export function NpmDetail({ data }: { data: NpmData }) {
  if (!data) return null;

  return (
    <div className="space-y-6">
      {/* Overview */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="Proxy Hosts" value={data.proxy_count} color="text-sky-400" />
        <StatCard label="Enabled" value={data.online_count} color="text-emerald-400" />
        <StatCard label="Disabled" value={data.offline_count} color="text-slate-400" />
        <StatCard label="SSL Certs" value={data.cert_count} color="text-violet-400" />
        <StatCard
          label="Expiring"
          value={data.certs_expiring_soon + data.certs_expired}
          color={data.certs_expiring_soon + data.certs_expired > 0 ? 'text-amber-400' : 'text-emerald-400'}
        />
      </div>

      {/* Proxy Hosts */}
      <Section title="Proxy Hosts" icon={Globe} iconColor="text-sky-400" count={data.proxy_count}>
        <GlassCard>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-3 py-2 text-slate-500 font-medium">Domain</th>
                  <th className="text-left px-3 py-2 text-slate-500 font-medium">Forward To</th>
                  <th className="text-left px-3 py-2 text-slate-500 font-medium">SSL</th>
                  <th className="text-left px-3 py-2 text-slate-500 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.proxy_hosts.map((h) => (
                  <tr key={h.id} className="border-b border-white/[0.04]">
                    <td className="px-3 py-2">
                      <div>
                        <span className="text-slate-200 font-mono">{h.domain_primary}</span>
                        {h.domains.length > 1 && (
                          <span className="text-slate-500 ml-1">+{h.domains.length - 1}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-slate-400 font-mono">{h.forward}</td>
                    <td className="px-3 py-2">
                      {h.certificate_id > 0 ? (
                        <span className="flex items-center gap-1 text-emerald-400">
                          <Lock size={10} /> {h.ssl_forced ? 'Forced' : 'On'}
                        </span>
                      ) : (
                        <span className="text-slate-500">None</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <StatusDot status={h.enabled ? 'online' : 'disabled'} />
                        <span className={h.enabled ? 'text-emerald-400' : 'text-slate-500'}>
                          {h.enabled ? 'Active' : 'Disabled'}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      </Section>

      {/* SSL Certificates */}
      <Section title="SSL Certificates" icon={Lock} iconColor="text-violet-400" count={data.cert_count}>
        <div className="space-y-2">
          {data.certificates.map((cert) => (
            <GlassCard key={cert.id} className="p-3">
              <div className="flex items-center gap-3">
                <StatusDot status={certDotStatus(cert.days_left)} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-slate-200 font-medium truncate">
                    {cert.nice_name || cert.domains[0] || `Cert #${cert.id}`}
                  </p>
                  <p className="text-xs text-slate-500 font-mono truncate">
                    {cert.domains.join(', ')}
                  </p>
                </div>
                <Badge>{cert.provider}</Badge>
                <span className={`text-xs font-mono ${certColor(cert.days_left)}`}>
                  {cert.days_left !== null ? (
                    cert.days_left <= 0 ? 'Expired' : `${cert.days_left}d`
                  ) : '—'}
                </span>
              </div>
            </GlassCard>
          ))}
          {data.certificates.length === 0 && (
            <p className="text-sm text-slate-500 text-center py-4">No certificates</p>
          )}
        </div>
      </Section>

      {/* Redirections */}
      {data.redir_count > 0 && (
        <Section title="Redirections" icon={ArrowRight} iconColor="text-amber-400" count={data.redir_count}>
          <GlassCard>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/[0.06]">
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Source</th>
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Target</th>
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Code</th>
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.redirections.map((r) => (
                    <tr key={r.id} className="border-b border-white/[0.04]">
                      <td className="px-3 py-2 text-slate-200 font-mono">
                        {r.domains[0] || '—'}
                        {r.domains.length > 1 && <span className="text-slate-500 ml-1">+{r.domains.length - 1}</span>}
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono">{r.forward_scheme}://{r.forward_url}</td>
                      <td className="px-3 py-2"><Badge>{r.forward_code}</Badge></td>
                      <td className="px-3 py-2">
                        <span className={r.enabled ? 'text-emerald-400' : 'text-slate-500'}>
                          {r.enabled ? 'Active' : 'Disabled'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </GlassCard>
        </Section>
      )}

      {/* Streams */}
      {data.stream_count > 0 && (
        <Section title="Streams" icon={Radio} iconColor="text-cyan-400" count={data.stream_count}>
          <GlassCard>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/[0.06]">
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Incoming Port</th>
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Forward To</th>
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Protocol</th>
                    <th className="text-left px-3 py-2 text-slate-500 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.streams.map((s) => (
                    <tr key={s.id} className="border-b border-white/[0.04]">
                      <td className="px-3 py-2 text-slate-200 font-mono">:{s.incoming_port}</td>
                      <td className="px-3 py-2 text-slate-400 font-mono">{s.forwarding_host}:{s.forwarding_port}</td>
                      <td className="px-3 py-2">
                        {s.tcp && <Badge>TCP</Badge>}
                        {s.udp && <Badge>UDP</Badge>}
                      </td>
                      <td className="px-3 py-2">
                        <span className={s.enabled ? 'text-emerald-400' : 'text-slate-500'}>
                          {s.enabled ? 'Active' : 'Disabled'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </GlassCard>
        </Section>
      )}

      {/* 404 Hosts */}
      {data.dead_count > 0 && (
        <Section title="404 Hosts" icon={Skull} iconColor="text-red-400" count={data.dead_count}>
          <div className="space-y-2">
            {data.dead_hosts.map((d) => (
              <GlassCard key={d.id} className="p-3">
                <div className="flex items-center gap-3">
                  <Skull size={14} className="text-red-400" />
                  <span className="text-sm text-slate-200 font-mono">{d.domains.join(', ')}</span>
                  <span className={`text-xs ml-auto ${d.enabled ? 'text-emerald-400' : 'text-slate-500'}`}>
                    {d.enabled ? 'Active' : 'Disabled'}
                  </span>
                </div>
              </GlassCard>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
