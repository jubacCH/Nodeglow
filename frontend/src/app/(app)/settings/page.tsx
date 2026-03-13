'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useState } from 'react';

type Tab = 'system' | 'monitoring' | 'notifications' | 'appearance' | 'api';

const ACCENT_COLORS = [
  { name: 'Sky', value: '#0ea5e9' },
  { name: 'Violet', value: '#8b5cf6' },
  { name: 'Emerald', value: '#10b981' },
  { name: 'Rose', value: '#f43f5e' },
  { name: 'Amber', value: '#f59e0b' },
  { name: 'Cyan', value: '#06b6d4' },
];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('system');
  const [accentColor, setAccentColor] = useState('#0ea5e9');
  const [sidebarPosition, setSidebarPosition] = useState<'left' | 'right'>('left');
  const [density, setDensity] = useState<'comfortable' | 'compact'>('comfortable');
  const [fontSize, setFontSize] = useState<'sm' | 'base' | 'lg'>('sm');

  const tabs: { key: Tab; label: string }[] = [
    { key: 'system', label: 'System' },
    { key: 'monitoring', label: 'Monitoring' },
    { key: 'notifications', label: 'Notifications' },
    { key: 'appearance', label: 'Appearance' },
    { key: 'api', label: 'API' },
  ];

  return (
    <div>
      <PageHeader
        title="Settings"
        description="Configure Nodeglow"
      />

      {/* Admin gate */}
      <GlassCard className="p-3 mb-6 border-amber-500/30 bg-amber-500/5">
        <div className="flex items-center gap-2">
          <Badge variant="severity" severity="warning">Admin</Badge>
          <p className="text-xs text-amber-300">Some settings require admin privileges to modify.</p>
        </div>
      </GlassCard>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/[0.06] mb-6">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'text-sky-400 border-b-2 border-sky-400'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'system' && (
        <div className="space-y-4">
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">General</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Instance Name</label>
                <input
                  type="text"
                  defaultValue="Nodeglow"
                  className="w-full max-w-sm px-3 py-1.5 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Timezone</label>
                <input
                  type="text"
                  defaultValue="Europe/Zurich"
                  className="w-full max-w-sm px-3 py-1.5 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
                />
              </div>
            </div>
          </GlassCard>
          <div className="flex justify-end">
            <Button size="sm">Save Changes</Button>
          </div>
        </div>
      )}

      {activeTab === 'monitoring' && (
        <div className="space-y-4">
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Ping Settings</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Check Interval (seconds)</label>
                <input
                  type="number"
                  defaultValue={60}
                  className="w-32 px-3 py-1.5 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Timeout (ms)</label>
                <input
                  type="number"
                  defaultValue={5000}
                  className="w-32 px-3 py-1.5 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
                />
              </div>
            </div>
          </GlassCard>
          <div className="flex justify-end">
            <Button size="sm">Save Changes</Button>
          </div>
        </div>
      )}

      {activeTab === 'notifications' && (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Notification Channels</h3>
          <p className="text-sm text-slate-500">Configure email, Slack, Discord, or webhook notification targets.</p>
          <div className="mt-4">
            <Button size="sm" variant="ghost">Add Channel</Button>
          </div>
        </GlassCard>
      )}

      {activeTab === 'appearance' && (
        <div className="space-y-4">
          {/* Accent color */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Accent Color</h3>
            <div className="flex gap-3">
              {ACCENT_COLORS.map((c) => (
                <button
                  key={c.value}
                  onClick={() => setAccentColor(c.value)}
                  className={`w-8 h-8 rounded-full border-2 transition-all ${
                    accentColor === c.value ? 'border-white scale-110' : 'border-transparent'
                  }`}
                  style={{ backgroundColor: c.value }}
                  title={c.name}
                />
              ))}
            </div>
          </GlassCard>

          {/* Sidebar position */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Sidebar Position</h3>
            <div className="flex gap-2">
              {(['left', 'right'] as const).map((pos) => (
                <button
                  key={pos}
                  onClick={() => setSidebarPosition(pos)}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    sidebarPosition === pos
                      ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {pos.charAt(0).toUpperCase() + pos.slice(1)}
                </button>
              ))}
            </div>
          </GlassCard>

          {/* Density */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Density</h3>
            <div className="flex gap-2">
              {(['comfortable', 'compact'] as const).map((d) => (
                <button
                  key={d}
                  onClick={() => setDensity(d)}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    density === d
                      ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {d.charAt(0).toUpperCase() + d.slice(1)}
                </button>
              ))}
            </div>
          </GlassCard>

          {/* Font size */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Font Size</h3>
            <div className="flex gap-2">
              {([
                { key: 'sm' as const, label: 'Small' },
                { key: 'base' as const, label: 'Default' },
                { key: 'lg' as const, label: 'Large' },
              ]).map((f) => (
                <button
                  key={f.key}
                  onClick={() => setFontSize(f.key)}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    fontSize === f.key
                      ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </GlassCard>

          <div className="flex justify-end">
            <Button size="sm">Save Preferences</Button>
          </div>
        </div>
      )}

      {activeTab === 'api' && (
        <div className="space-y-4">
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">API Keys</h3>
            <p className="text-sm text-slate-500 mb-4">
              Manage API keys for programmatic access. Keys use the <code className="text-sky-400">ng_</code> prefix.
            </p>
            <Button size="sm" variant="ghost">Create API Key</Button>
          </GlassCard>
        </div>
      )}
    </div>
  );
}
