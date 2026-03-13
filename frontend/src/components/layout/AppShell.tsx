'use client';

import { useEffect, type ReactNode } from 'react';
import { Sidebar } from './Sidebar';
import { ToastContainer } from '@/components/ui/Toast';
import { useAuthStore } from '@/stores/auth';
import { useWsStore } from '@/stores/websocket';
import { useThemeStore } from '@/stores/theme';
import { cn } from '@/lib/utils';

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const connect = useWsStore((s) => s.connect);
  const sidebarPosition = useThemeStore((s) => s.sidebarPosition);

  useEffect(() => {
    fetchUser();
    connect();
  }, [fetchUser, connect]);

  return (
    <div className={cn('flex h-screen', sidebarPosition === 'right' && 'flex-row-reverse')}>
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-[#0B0E14]">
        <div className="max-w-7xl mx-auto px-6 py-6">
          {children}
        </div>
      </main>
      <ToastContainer />
    </div>
  );
}
