'use client';

import Link from 'next/link';
import { Server, Plug, Cpu, Sparkles, Copy, Check } from 'lucide-react';
import { useState } from 'react';
import { Card } from '@/components/ui/Card';

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
      <Card className="mb-6 p-8 text-center">
        <div className="mb-4 inline-flex rounded-full bg-accent-soft p-3">
          <Sparkles size={28} className="text-accent" aria-hidden="true" />
        </div>
        <h2 className="mb-2 font-display text-h3 font-semibold tracking-[-0.03em] text-fg">
          Welcome to Nodeglow
        </h2>
        <p className="mx-auto max-w-xl text-ui text-fg-2">
          Your dashboard is empty because there&apos;s nothing to monitor yet.
          Pick one of the three options below to get data flowing — you can
          mix and match later.
        </p>
      </Card>

      {/* Three paths */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <PathCard
          icon={Server}
          title="1. Add a Host"
          description="Monitor an IP, hostname, or URL via ICMP / TCP / HTTP. The simplest way to start."
          ctaHref="/hosts"
          ctaLabel="Add Host"
        />
        <PathCard
          icon={Plug}
          title="2. Connect an Integration"
          description="Plug in Proxmox, UniFi, TrueNAS, Pi-hole, Home Assistant — Nodeglow knows 15 stacks."
          ctaHref="/integration/store"
          ctaLabel="Browse Integrations"
        />
        <PathCard
          icon={Cpu}
          title="3. Install an Agent"
          description="Lightweight agent reports CPU, memory, disks, network, and processes from Linux or Windows hosts."
          ctaHref="/agents"
          ctaLabel="Manage Agents"
        />
      </div>

      {/* Inline install commands */}
      <Card className="p-6">
        <h3 className="mb-1 text-body font-medium text-fg">
          One-liner agent install
        </h3>
        <p className="mb-4 text-meta text-fg-3">
          Run these on the host you want to monitor. They auto-enrol against
          this Nodeglow instance.
        </p>
        <div className="space-y-3">
          <CommandLine label="Linux / macOS" command={linuxCmd} />
          <CommandLine label="Windows (PowerShell)" command={windowsCmd} />
        </div>
      </Card>
    </div>
  );
}

function PathCard({
  icon: Icon,
  title,
  description,
  ctaHref,
  ctaLabel,
}: {
  icon: typeof Server;
  title: string;
  description: string;
  ctaHref: string;
  ctaLabel: string;
}) {
  return (
    <Card className="flex flex-col p-5">
      <div className="mb-3 self-start rounded-ctl bg-surface-2 p-2 text-accent">
        <Icon size={18} aria-hidden="true" />
      </div>
      <h3 className="mb-1 text-ui font-medium text-fg">{title}</h3>
      <p className="mb-4 flex-1 text-meta text-fg-2">{description}</p>
      <Link
        href={ctaHref}
        className="inline-flex items-center gap-1.5 text-ui font-medium text-accent hover:text-accent-hover"
      >
        {ctaLabel} →
      </Link>
    </Card>
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
      <div className="mb-1 text-meta text-fg-2">
        {label}
      </div>
      <div
        className="flex items-center gap-2 rounded-ctl border border-border bg-bg px-3 py-2 font-mono text-meta"
      >
        <code className="flex-1 select-all truncate text-fg-2">{command}</code>
        <button
          type="button"
          onClick={onCopy}
          className="rounded-ng-sm p-1 text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg"
          aria-label={copied ? 'Copied' : 'Copy to clipboard'}
        >
          {copied ? <Check size={14} className="text-ok" aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
        </button>
      </div>
    </div>
  );
}
