'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';

export default function CredentialsPage() {
  return (
    <div>
      <PageHeader title="Credentials" description="Stored credentials for integrations" />
      <GlassCard className="p-8">
        <p className="text-center text-sm text-slate-500">Coming soon</p>
      </GlassCard>
    </div>
  );
}
