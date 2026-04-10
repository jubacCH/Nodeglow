'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { Sidebar } from './Sidebar';
import { GlowPanel } from '@/components/copilot/CopilotPanel';
import { ToastContainer } from '@/components/ui/Toast';
import { KeyboardShortcuts } from '@/components/ui/KeyboardShortcuts';
import { CommandPaletteHost } from '@/components/ui/CommandPaletteHost';
import { useAuthStore } from '@/stores/auth';
import { useWsStore } from '@/stores/websocket';
import { useThemeStore } from '@/stores/theme';
import { cn } from '@/lib/utils';
import { Menu, X } from 'lucide-react';

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const connect = useWsStore((s) => s.connect);
  const { sidebarPosition, accentColor, colorMode, density, fontSize } = useThemeStore();
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    fetchUser();
    connect();
  }, [fetchUser, connect]);

  // Close mobile sidebar on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Apply theme settings as CSS custom properties
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--accent', accentColor);
    root.style.setProperty('--font-size-base', `${fontSize}px`);
    root.style.fontSize = `${fontSize}px`;
    root.setAttribute('data-theme', colorMode);
  }, [accentColor, fontSize, colorMode]);

  return (
    <div className={cn(
      'flex h-screen',
      sidebarPosition === 'right' && 'flex-row-reverse',
      density === 'compact' && 'text-sm [&_*]:leading-tight',
    )}>
      {/* Desktop sidebar */}
      <div className="hidden lg:block">
        <Sidebar />
      </div>

      {/* Mobile sidebar overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <div className={cn(
            'absolute top-0 bottom-0 w-[280px] z-50 animate-in slide-in-from-left duration-200',
            sidebarPosition === 'right' ? 'right-0' : 'left-0',
          )}>
            <Sidebar />
          </div>
        </div>
      )}

      <main
        className="flex-1 overflow-y-auto flex flex-col"
        style={{
          background: 'var(--ng-bg)',
          backgroundImage: 'var(--ng-body-gradient)',
          // No fixed attachment here — the gradient travels with the scroll
          // surface, so empty space at the bottom of short pages still looks
          // like the rest of the page instead of a black bar.
          overscrollBehavior: 'contain',
        }}
      >
        {/* Mobile header bar */}
        <div className="lg:hidden flex items-center gap-3 px-4 h-14 sticky top-0 z-40" style={{ background: 'var(--ng-bg)', borderBottom: '1px solid var(--ng-glass-border)' }}>
          <button
            onClick={() => setMobileOpen(!mobileOpen)}
            className="p-1.5 rounded-md transition-colors"
            style={{ color: 'var(--ng-text-muted)' }}
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo-icon.svg" alt="Nodeglow" className="w-6 h-6" />
          <span className="text-sm font-semibold bg-gradient-to-r from-sky-400 to-violet-400 bg-clip-text text-transparent">
            Nodeglow
          </span>
        </div>
        <div
          key={pathname}
          className={cn(
            // flex-1 makes the content wrapper fill the main height even
            // when the page is shorter than the viewport — no more empty
            // area at the bottom of short pages.
            'flex-1 max-w-[1920px] w-full mx-auto animate-page-enter',
            density === 'compact' ? 'px-4 py-4' : 'px-6 py-6',
          )}
        >
          {children}
        </div>
      </main>
      <GlowPanel />
      <ToastContainer />
      <KeyboardShortcuts />
      <CommandPaletteHost />
    </div>
  );
}
