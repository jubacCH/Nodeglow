'use client';

import { Suspense, useEffect, type ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { GlowPanel } from '@/components/copilot/CopilotPanel';
import { ToastContainer } from '@/components/ui/Toast';
import { KeyboardShortcuts } from '@/components/ui/KeyboardShortcuts';
import { CommandPaletteHost } from '@/components/ui/CommandPaletteHost';
import { NodeglowMark } from '@/components/ui/NodeglowMark';
import { useAuthStore } from '@/stores/auth';
import { useWsStore } from '@/stores/websocket';
import { useThemeStore } from '@/stores/theme';
import { cn } from '@/lib/utils';
import { loginHref } from '@/lib/redirect';
import { Rail } from './Rail';
import { TopBar } from './TopBar';
import { LicenseBanner } from './LicenseBanner';
import { SubNav } from './SubNav';
import { MobileTabBar } from './MobileTabBar';

interface AppShellProps {
  children: ReactNode;
}

/**
 * E3 app shell: slim icon rail (≥760px) or bottom tab bar (<760px), top bar,
 * section sub-navigation and the page. Layout tokens: --ng-rail-w,
 * --ng-topbar-h, --ng-tabbar-h, --ng-content-max, --ng-gutter.
 */
export function AppShell({ children }: AppShellProps) {
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const user = useAuthStore((s) => s.user);
  const isLoading = useAuthStore((s) => s.isLoading);
  const connect = useWsStore((s) => s.connect);
  const disconnect = useWsStore((s) => s.disconnect);
  const queryClient = useQueryClient();
  const density = useThemeStore((s) => s.density);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    fetchUser();
    // Live events are folded into the query cache (see lib/liveUpdates).
    connect(queryClient);
    // Tear down the socket + reconnect loop on unmount/logout to avoid leaks.
    return () => disconnect();
  }, [fetchUser, connect, disconnect, queryClient]);

  // Redirect to /login once auth state resolves and there is no user.
  useEffect(() => {
    if (!isLoading && !user) {
      router.replace(loginHref(window.location.pathname + window.location.search));
    }
  }, [isLoading, user, router]);

  // Do not render protected app children until auth state is resolved.
  if (isLoading || !user) {
    return (
      <div className="grid h-screen place-items-center bg-bg" aria-busy="true" aria-label="Loading">
        <NodeglowMark size={40} className="animate-pulse text-fg-3" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg">
      <a
        href="#main"
        className="sr-only z-toast rounded-ctl bg-accent-btn px-3 py-2 text-ui font-medium text-on-accent focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>
      {/* useSearchParams inside the nav needs a Suspense boundary */}
      <Suspense fallback={null}>
        <Rail />
      </Suspense>
      <div className="pl-rail max-[759px]:pl-0">
        <TopBar />
        <LicenseBanner />
        <main
          id="main"
          tabIndex={-1}
          className={cn(
            'min-w-0 overflow-x-clip outline-none',
            density === 'compact' ? 'px-4 pb-10 pt-5' : 'px-gutter pb-12 pt-7',
            'max-[759px]:px-4 max-[759px]:pb-[90px] max-[759px]:pt-5',
          )}
        >
          <div className="mx-auto w-full max-w-content">
            <Suspense fallback={null}>
              <SubNav />
            </Suspense>
            {/* One page transition: remount + CSS fade (off with reduced motion). */}
            <div key={pathname} className={cn('animate-page-enter', density === 'compact' && 'text-sm [&_*]:leading-tight')}>
              {children}
            </div>
          </div>
        </main>
      </div>
      <Suspense fallback={null}>
        <MobileTabBar />
      </Suspense>
      <GlowPanel />
      <ToastContainer />
      <KeyboardShortcuts />
      <CommandPaletteHost />
    </div>
  );
}
