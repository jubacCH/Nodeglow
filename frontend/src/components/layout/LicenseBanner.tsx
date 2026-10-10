'use client';

import Link from 'next/link';
import { KeyRound } from 'lucide-react';
import { licenseBanner, useFeatures } from '@/hooks/queries/useFeatures';
import { useIsAdmin } from '@/stores/auth';
import { cn } from '@/lib/utils';

const TONES = {
  warning: 'border-warning/30 bg-warning-soft text-warning',
  down: 'border-down/30 bg-down-soft text-down',
} as const;

/**
 * One calm line under the top bar for admins when the enterprise license is
 * in its grace period, expired or unusable. Monitoring is never affected, so
 * it informs and links to Settings → License; nothing else in the shell
 * changes.
 */
export function LicenseBanner() {
  const isAdmin = useIsAdmin();
  const { data: features } = useFeatures(isAdmin);
  const banner = isAdmin ? licenseBanner(features) : null;
  if (!banner) return null;
  return (
    <div
      role="status"
      data-testid="license-banner"
      className={cn(
        'flex items-center gap-2 border-b px-gutter py-1.5 text-meta max-[759px]:px-4',
        TONES[banner.tone],
      )}
    >
      <KeyRound size={14} aria-hidden="true" className="shrink-0" />
      <span className="min-w-0 flex-1">{banner.text}</span>
      <Link href="/settings?tab=license" className="shrink-0 font-medium underline-offset-2 hover:underline">
        License settings
      </Link>
    </div>
  );
}
