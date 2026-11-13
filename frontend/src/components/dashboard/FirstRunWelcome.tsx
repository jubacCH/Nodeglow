'use client';

import Link from 'next/link';
import { Server, Plug, Cpu } from 'lucide-react';
import { Card } from '@/components/ui/Card';

/**
 * Shown on the dashboard when there are zero hosts, zero integrations, and
 * zero agents. Offers the three ways to get data into Nodeglow; each link
 * opens the dialog that does it. Install commands are not shown here: they
 * need a per-install token, which the "Add agent" dialog creates.
 */
export function FirstRunWelcome() {
  return (
    <div className="max-w-4xl mx-auto">
      <Card className="mb-6 p-8 text-center">
        <h2 className="mb-2 font-display text-h3 font-semibold tracking-[-0.03em] text-fg">
          Welcome to Nodeglow
        </h2>
        <p className="mx-auto max-w-xl text-ui text-fg-2">
          Nothing is being monitored yet. Start with one of the three options
          below. You can combine them later.
        </p>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <PathCard
          icon={Server}
          title="1. Add a host"
          description="Check a server, device or website by ping, port or HTTP. The quickest way to start."
          ctaHref="/hosts?add=1"
          ctaLabel="Add host"
        />
        <PathCard
          icon={Plug}
          title="2. Connect an integration"
          description="Read status from systems you already run, such as a hypervisor, a network controller or a NAS."
          ctaHref="/integration/store"
          ctaLabel="Browse integrations"
        />
        <PathCard
          icon={Cpu}
          title="3. Install an agent"
          description="A small agent reports CPU, memory, disks, services and logs from Linux and Windows machines."
          ctaHref="/agents?add=1"
          ctaLabel="Add agent"
        />
      </div>
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
