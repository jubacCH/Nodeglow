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
  const { sidebarPosition, accentColor, density, fontSize } = useThemeStore();

  useEffect(() => {
    fetchUser();
    connect();
  }, [fetchUser, connect]);

  // Apply theme settings as CSS custom properties
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--accent', accentColor);
    root.style.setProperty('--font-size-base', `${fontSize}px`);
    root.style.fontSize = `${fontSize}px`;
  }, [accentColor, fontSize]);

  return (
    <div className={cn(
      'flex h-screen',
      sidebarPosition === 'right' && 'flex-row-reverse',
      density === 'compact' && 'text-sm [&_*]:leading-tight',
    )}>
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-[#0B0E14]">
        <div className={cn(
          'max-w-7xl mx-auto',
          density === 'compact' ? 'px-4 py-4' : 'px-6 py-6',
        )}>
          {children}
        </div>
      </main>
      <ToastContainer />
    </div>
  );
}
