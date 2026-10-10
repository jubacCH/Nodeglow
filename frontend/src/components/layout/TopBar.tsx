'use client';

import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Keyboard, LogOut, Moon, Search, Sparkles, Sun } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ColorMode } from '@/lib/theme';
import { useResolvedTheme } from '@/lib/useResolvedTheme';
import { useAuthStore } from '@/stores/auth';
import { useThemeStore } from '@/stores/theme';
import { useUiStore } from '@/stores/ui';
import { useWsStore } from '@/stores/websocket';
import { useGlowStore } from '@/stores/glow';
import { useAiStatus } from '@/hooks/queries/useAiStatus';
import { hasFeature, useFeatures } from '@/hooks/queries/useFeatures';
import { IconButton } from '@/components/ui/Button';
import { Kbd } from '@/components/ui/Kbd';
import { Popover, PopoverItem } from '@/components/ui/Popover';
import { SegmentedControl } from '@/components/ui/Tabs';
import { Tag } from '@/components/ui/Tag';
import { Tooltip } from '@/components/ui/Tooltip';
import { formatAsOf } from '@/components/ui/QueryState';

/** Tenant context. Multi-tenancy is planned; the control says so. */
function TenantIndicator() {
  return (
    <Popover
      label="Tenant"
      placement="bottom-start"
      trigger={(props) => (
        <button
          {...props}
          type="button"
          className="flex h-[36px] min-w-0 items-center gap-2 rounded-ctl pl-1.5 pr-2.5 hover:bg-surface-2"
        >
          <span aria-hidden="true" className="grid h-6 w-6 shrink-0 place-items-center rounded-[6px] bg-surface-3 text-micro font-semibold text-fg-2">
            DT
          </span>
          <span className="truncate text-ui font-medium text-fg max-[420px]:max-w-[110px]">Default tenant</span>
          <ChevronDown size={14} aria-hidden="true" className="shrink-0 text-fg-3" />
          <Tag planned className="max-[759px]:hidden">Multi-tenant · Planned</Tag>
        </button>
      )}
    >
      <div className="flex items-center gap-2.5 rounded-ng-sm bg-surface-2 px-2.5 py-[9px] text-ui">
        <span aria-hidden="true" className="grid h-[22px] w-[22px] place-items-center rounded-[6px] bg-surface-3 text-[10px] font-semibold text-fg-2">DT</span>
        <span className="flex-1">Default tenant</span>
        <span className="text-meta text-fg-3">Current</span>
      </div>
      <p className="mt-1 border-t border-border px-2.5 pb-1.5 pt-2 text-meta text-fg-3">
        Switching between customers (multi-tenant) is <b className="font-medium text-fg-2">planned</b> and not available yet.
      </p>
    </Popover>
  );
}

function SearchButton() {
  const open = useUiStore((s) => s.setPaletteOpen);
  return (
    <button
      type="button"
      onClick={() => open(true)}
      aria-label="Search (Ctrl+K)"
      aria-keyshortcuts="Control+K Meta+K"
      className={cn(
        'flex h-[36px] w-[300px] max-w-[32vw] items-center gap-2 rounded-ctl border border-border bg-surface pl-3 pr-2 text-fg-3 hover:border-border-2',
        'max-[899px]:w-[36px] max-[899px]:max-w-none max-[899px]:justify-center max-[899px]:border-transparent max-[899px]:bg-transparent max-[899px]:p-0',
      )}
    >
      <Search size={16} aria-hidden="true" className="shrink-0" />
      <span className="flex-1 truncate text-left text-ui max-[899px]:hidden">Search hosts, pages…</span>
      <Kbd className="max-[899px]:hidden">Ctrl K</Kbd>
    </button>
  );
}

/**
 * Live connection (WebSocket). Healthy is quiet (no glow, no colour block);
 * a lost connection is an explicit degraded pill with its start time, so
 * live views are never mistaken for current.
 */
function ConnectionStatus() {
  const isConnected = useWsStore((s) => s.isConnected);
  const everConnected = useWsStore((s) => s.everConnected);
  const changedAt = useWsStore((s) => s.changedAt);
  const connect = useWsStore((s) => s.connect);
  const qc = useQueryClient();

  if (isConnected) {
    return (
      <span role="status" className="flex items-center gap-1.5 px-1 text-meta text-fg-3 max-[759px]:hidden">
        <span aria-hidden="true" className="h-[6px] w-[6px] rounded-full bg-ok" />
        Live
      </span>
    );
  }
  if (!everConnected) {
    return (
      <span role="status" className="flex items-center gap-1.5 px-1 text-meta text-fg-3 max-[759px]:hidden">
        <span aria-hidden="true" className="h-[6px] w-[6px] rounded-full shadow-[inset_0_0_0_1.5px_var(--ng-st-unknown)]" />
        Connecting…
      </span>
    );
  }
  const since = formatAsOf(changedAt);
  return (
    <span role="status" className="inline-flex">
      <button
        type="button"
        onClick={() => connect(qc)}
        title="Live updates are paused; data is polled. Click to reconnect."
        className="inline-flex h-[28px] items-center gap-1.5 whitespace-nowrap rounded-[14px] border border-degraded/45 bg-degraded/10 px-2.5 text-meta font-medium text-degraded"
      >
        <span aria-hidden="true" className="h-[6px] w-[6px] rounded-full bg-current" />
        <span className="max-[420px]:hidden">Live paused{since ? ` since ${since}` : ''} · polling</span>
        <span className="hidden max-[420px]:inline">Paused</span>
        <span className="sr-only">. Reconnect</span>
      </button>
    </span>
  );
}

function ThemeToggle() {
  const theme = useResolvedTheme();
  const toggle = useThemeStore((s) => s.toggleColorMode);
  const label = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
  return (
    <Tooltip content={label} side="bottom">
      <IconButton aria-label={label} onClick={toggle} className="text-fg-2">
        {theme === 'dark' ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
      </IconButton>
    </Tooltip>
  );
}

function GlowToggle() {
  const user = useAuthStore((s) => s.user);
  const { data: features } = useFeatures(!!user);
  const installed = hasFeature(features, 'ai_assistant');
  const { data: ai } = useAiStatus(!!user && installed);
  const isOpen = useGlowStore((s) => s.isOpen);
  const toggle = useGlowStore((s) => s.toggle);
  // Glow is an enterprise feature: no button at all where it is not installed.
  if (!installed || !ai?.available) return null;
  return (
    <Tooltip content="Glow assistant" side="bottom">
      <IconButton aria-label="Glow assistant" aria-pressed={isOpen} onClick={toggle} className={isOpen ? 'bg-accent-soft text-accent' : 'text-fg-2'}>
        <Sparkles size={18} aria-hidden="true" />
      </IconButton>
    </Tooltip>
  );
}

function UserMenu() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const colorMode = useThemeStore((s) => s.colorMode);
  const setColorMode = useThemeStore((s) => s.setColorMode);
  const setShortcutsOpen = useUiStore((s) => s.setShortcutsOpen);
  if (!user) return null;
  const initials = user.username.slice(0, 2).toUpperCase();
  return (
    <Popover
      label="Account"
      placement="bottom-end"
      className="w-[260px]"
      trigger={(props) => (
        <button
          {...props}
          type="button"
          aria-label={`Account: ${user.username}`}
          className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-surface-3 text-meta font-semibold text-fg-2 hover:text-fg"
        >
          {initials}
        </button>
      )}
    >
      {(close) => (
        <>
          <div className="px-2.5 pb-2.5 pt-2">
            <p className="truncate text-ui font-medium text-fg">{user.username}</p>
            <p className="text-meta capitalize text-fg-3">{user.role}</p>
          </div>
          <div className="border-t border-border px-2.5 py-2.5">
            <p className="mb-1.5 text-meta text-fg-2">Theme</p>
            <SegmentedControl<ColorMode>
              label="Theme"
              size="sm"
              value={colorMode}
              onChange={setColorMode}
              options={[
                { value: 'dark', label: 'Dark' },
                { value: 'light', label: 'Light' },
                { value: 'system', label: 'System' },
              ]}
            />
          </div>
          <div className="border-t border-border pt-1">
            <PopoverItem icon={<Keyboard size={16} />} onClick={() => { close(); setShortcutsOpen(true); }}>
              Keyboard shortcuts
            </PopoverItem>
            <PopoverItem icon={<LogOut size={16} />} onClick={logout}>
              Log out
            </PopoverItem>
          </div>
        </>
      )}
    </Popover>
  );
}

/** E3 top bar: tenant, search (Ctrl+K), connection, Glow, theme, account. */
export function TopBar() {
  return (
    <header className="sticky top-0 z-topbar flex h-topbar items-center gap-2.5 border-b border-border bg-bg px-gutter max-[759px]:gap-1.5 max-[759px]:px-4">
      <TenantIndicator />
      <span className="flex-1" />
      <SearchButton />
      <ConnectionStatus />
      <GlowToggle />
      <ThemeToggle />
      <UserMenu />
    </header>
  );
}
