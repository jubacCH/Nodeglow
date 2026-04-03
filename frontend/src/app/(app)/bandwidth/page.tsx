'use client';

import { useEffect } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';

export default function BandwidthPage() {
  useEffect(() => { document.title = 'Bandwidth | Nodeglow'; }, []);

  return (
    <div>
      <PageHeader title="Bandwidth" description="Network traffic monitoring" />
      <GlassCard className="p-4">
        <p className="text-slate-300">Bandwidth monitoring page - loading...</p>
      </GlassCard>
    </div>
  );
}
