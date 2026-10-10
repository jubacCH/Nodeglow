'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { GlassCard } from '@/components/ui/GlassCard';

/**
 * Route-level error boundary for every page inside the app shell. The
 * sidebar stays usable; only the page area is replaced.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[AppError]', error);
  }, [error]);

  return (
    <GlassCard className="max-w-xl mx-auto mt-12">
      <div role="alert" className="flex flex-col items-center text-center gap-3 px-6 py-12">
        <AlertTriangle size={36} className="text-amber-400" aria-hidden="true" />
        <h1 className="text-lg font-semibold text-slate-200">This page crashed</h1>
        <p className="text-sm text-slate-500 max-w-md">
          Something went wrong while rendering it. You can try again, or go back to the dashboard.
        </p>
        {error.message && (
          <p className="text-[11px] font-mono text-slate-600 max-w-full break-words">
            {error.message}
            {error.digest ? ` (${error.digest})` : ''}
          </p>
        )}
        <div className="flex items-center gap-3 mt-2">
          <button
            type="button"
            onClick={reset}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm text-slate-200 bg-white/[0.06] border border-white/[0.1] hover:bg-white/[0.1] transition-colors"
          >
            <RotateCcw size={14} aria-hidden="true" /> Try again
          </button>
          <Link href="/" className="text-sm text-sky-400 hover:text-sky-300">
            Dashboard
          </Link>
        </div>
      </div>
    </GlassCard>
  );
}
