'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';

export default function IncidentDetailPage() {
  return (
    <div>
      <PageHeader title="Incident Detail" description="Incident timeline and events" />
      <GlassCard className="p-8">
        <p className="text-center text-sm text-slate-500">Coming soon</p>
      </GlassCard>
    </div>
  );
}
