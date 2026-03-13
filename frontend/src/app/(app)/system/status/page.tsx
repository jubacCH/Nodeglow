'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';

export default function SystemStatusPage() {
  return (
    <div>
      <PageHeader title="System Status" description="Backend health and diagnostics" />
      <GlassCard className="p-8">
        <p className="text-center text-sm text-slate-500">Coming soon</p>
      </GlassCard>
    </div>
  );
}
