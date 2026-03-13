'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';

export default function SyslogTemplatesPage() {
  return (
    <div>
      <PageHeader title="Log Templates" description="Extracted log message templates" />
      <GlassCard className="p-8">
        <p className="text-center text-sm text-slate-500">Coming soon</p>
      </GlassCard>
    </div>
  );
}
