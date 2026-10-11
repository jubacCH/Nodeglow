'use client';

import Link from 'next/link';
import { LifeBuoy } from 'lucide-react';
import { useSupportAccess } from '@/hooks/queries/useSupportAccess';
import { supportBanner } from '@/lib/tenants';
import { useIsAdmin } from '@/stores/auth';

/**
 * One line under the top bar while support access to this tenant is active
 * (owner decision 8): every member sees that the operators can look in, and
 * the operator working through it sees that too. Admins get the link to end
 * it. Only in an installation with the multi-tenancy feature (ee).
 */
export function SupportAccessBanner() {
  const isAdmin = useIsAdmin();
  const { data } = useSupportAccess();
  const text = supportBanner(data);
  if (!text) return null;
  return (
    <div
      role="status"
      data-testid="support-banner"
      className="flex items-center gap-2 border-b border-accent/30 bg-accent-soft px-gutter py-1.5 text-meta text-accent max-[759px]:px-4"
    >
      <LifeBuoy size={14} aria-hidden="true" className="shrink-0" />
      <span className="min-w-0 flex-1">{text}</span>
      {isAdmin && data?.can_manage && (
        <Link href="/settings?tab=support" className="shrink-0 font-medium underline-offset-2 hover:underline">
          Manage support access
        </Link>
      )}
    </div>
  );
}
