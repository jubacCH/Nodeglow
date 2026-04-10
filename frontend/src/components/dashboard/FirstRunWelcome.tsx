'use client';

import Link from 'next/link';
import { Server, Plug, Cpu, Sparkles, Copy, Check } from 'lucide-react';
import { useState } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';

interface FirstRunWelcomeProps {
  /** Server URL for agent install commands. Defaults to current origin. */
  serverUrl?: string;
}

/**
 * Shown on the dashboard when there are zero hosts, zero integrations, and
 * zero agents. Walks the user through the three concrete ways to get data
 * flowing into Nodeglow with copy-paste install commands inline.
 */
export function FirstRunWelcome({ serverUrl }: FirstRunWelcomeProps) {
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  const url = serverUrl || origin;
  const linuxCmd = `curl -sSL ${url}/install/linux | sudo bash`;
  const windowsCmd = `irm ${url}/install/windows | iex`;

  return (
    <div className="max-w-4xl mx-auto">
      {/* Hero */}
      <GlassCard className="p-8 mb-6 text-center">
        <div className="inline-flex p-3 rounded-full bg-sky-500/10 mb-4">
          <Sparkles size={28} className="text-sky-400" />
        </div>
        <h2 className="text-2xl font-bold text-slate-100 mb-2">
          Welcome to Nodeglow
        </h2>
        <p className="text-sm text-slate-400 max-w-xl mx-auto">
          Your dashboard is empty because there&apos;s nothing to monitor yet.
          Pick one of the three options below to get data flowing — you can
          mix and match later.
        </p>
      </GlassCard>

      {/* Three paths */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <PathCard
          icon={Server}
          iconColor="text-sky-400"
          tint="bg-sky-500/10"
          title="1. Add a Host"
          description="Monitor an IP, hostname, or URL via ICMP / TCP / HTTP. The simplest way to start."
          ctaHref="/hosts"
          ctaLabel="Add Host"
        />
        <PathCard
          icon={Plug}
          iconColor="text-violet-400"
          tint="bg-violet-500/10"
          title="2. Connect an Integration"
          description="Plug in Proxmox, UniFi, TrueNAS, Pi-hole, Home Assistant — Nodeglow knows 15 stacks."
          ctaHref="/integration/store"
          ctaLabel="Browse Integrations"
        />
        <PathCard
          icon={Cpu}
          iconColor="text-emerald-400"
          tint="bg-emerald-500/10"
          title="3. Install an Agent"
          description="Lightweight agent reports CPU, memory, disks, network, and processes from Linux or Windows hosts."
          ctaHref="/agents"
          ctaLabel="Manage Agents"
        />
      </div>

      {/* Inline install commands */}
      <GlassCard className="p-6">
        <h3 className="text-sm font-semibold text-slate-200 mb-1">
          One-liner agent install
        </h3>
        <p className="text-xs text-slate-500 mb-4">
          Run these on the host you want to monitor. They auto-enrol against
          this Nodeglow instance.
        </p>
        <div className="space-y-3">
          <CommandLine label="Linux / macOS" command={linuxCmd} />
          <CommandLine label="Windows (PowerShell)" command={windowsCmd} />
        </div>
      </GlassCard>
    </div>
  );
}

function PathCard({
  icon: Icon,
  iconColor,
  tint,
  title,
  description,
  ctaHref,
  ctaLabel,
}: {
  icon: typeof Server;
  iconColor: string;
  tint: string;
  title: string;
  description: string;
  ctaHref: string;
  ctaLabel: string;
}) {
  return (
    <GlassCard className="p-5 flex flex-col">
      <div className={`p-2 rounded-lg ${tint} self-start mb-3`}>
        <Icon size={18} className={iconColor} />
      </div>
      <h3 className="text-sm font-semibold text-slate-200 mb-1">{title}</h3>
      <p className="text-xs text-slate-500 flex-1 mb-4">{description}</p>
      <Link
        href={ctaHref}
        className="text-xs font-medium text-sky-400 hover:text-sky-300 inline-flex items-center gap-1.5"
      >
        {ctaLabel} →
      </Link>
    </GlassCard>
  );
}

function CommandLine({ label, command }: { label: string; command: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  };
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">
        {label}
      </div>
      <div
        className="flex items-center gap-2 px-3 py-2 rounded-md font-mono text-xs"
        style={{
          background: 'var(--ng-surface-container-lowest)',
          border: '1px solid var(--ng-card-border)',
        }}
      >
        <code className="flex-1 text-slate-300 truncate select-all">{command}</code>
        <button
          type="button"
          onClick={onCopy}
          className="p-1 rounded hover:bg-slate-500/10 text-slate-400 hover:text-slate-200 transition-colors"
          aria-label={copied ? 'Copied' : 'Copy to clipboard'}
        >
          {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
        </button>
      </div>
    </div>
  );
}
