'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';

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
    <Card padding="none" className="mx-auto mt-12 max-w-xl">
      <div role="alert" className="flex flex-col items-center gap-3 px-6 py-12 text-center">
        <AlertTriangle size={36} className="text-warning" aria-hidden="true" />
        <h1 className="text-lead font-semibold text-fg">This page crashed</h1>
        <p className="max-w-md text-ui text-fg-3">
          Something went wrong while rendering it. You can try again, or go back to the dashboard.
        </p>
        {error.message && (
          <p className="max-w-full break-words font-mono text-micro text-fg-3">
            {error.message}
            {error.digest ? ` (${error.digest})` : ''}
          </p>
        )}
        <div className="mt-2 flex items-center gap-3">
          <Button variant="secondary" size="sm" onClick={reset}>
            <RotateCcw size={14} aria-hidden="true" /> Try again
          </Button>
          <Link href="/" className="text-ui text-accent hover:text-accent-hover">
            Dashboard
          </Link>
        </div>
      </div>
    </Card>
  );
}
