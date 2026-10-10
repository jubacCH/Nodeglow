'use client';

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { TabPanel, Tabs, type TabItem } from '@/components/ui/Tabs';
import { AiSettingsTab } from '@/components/settings/AiSettingsTab';
import { ApiTab } from '@/components/settings/ApiTab';
import { AppearanceTab } from '@/components/settings/AppearanceTab';
import { AuthTab } from '@/components/settings/AuthTab';
import { BackupTab } from '@/components/settings/BackupTab';
import { LicenseTab } from '@/components/settings/LicenseTab';
import { MonitoringTab, SystemTab } from '@/components/settings/GeneralTabs';
import { NotificationsTab, type NotifLog } from '@/components/settings/NotificationsTab';
import { useSaveStatus, useSectionForm } from '@/components/settings/formKit';
import {
  buildDigestBody, buildGeneralParams, buildNotificationParams, digestFromSettings, generalEquals,
  generalFromSettings, ldapFromSettings, notifFromSettings, type SettingsData,
} from '@/components/settings/settingsForm';
import { api, apiErrorBody, apiErrorMessage, get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';

type Tab = 'system' | 'monitoring' | 'notifications' | 'appearance' | 'api' | 'ai' | 'auth' | 'backup' | 'license';

const TAB_IDS: Tab[] = ['system', 'monitoring', 'notifications', 'appearance', 'api', 'ai', 'auth', 'backup', 'license'];
const TAB_LABELS: Record<Tab, string> = {
  system: 'System',
  monitoring: 'Monitoring',
  notifications: 'Notifications',
  appearance: 'Appearance',
  api: 'API',
  ai: 'AI',
  auth: 'Authentication',
  backup: 'Backup',
  license: 'License',
};

/** `message` of a failed notification save/test response, if any. */
function notifErrorMessage(err: unknown): string {
  const msg = apiErrorBody(err)?.message;
  return typeof msg === 'string' ? msg : '';
}

function FormSkeleton() {
  return (
    <Card aria-busy="true" aria-label="Loading">
      <Skeleton className="mb-4 h-5 w-40" />
      <div className="space-y-3">
        <Skeleton className="h-9 w-full max-w-xl" />
        <Skeleton className="h-9 w-full max-w-xl" />
        <Skeleton className="h-9 w-full max-w-xl" />
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  useEffect(() => { document.title = 'Settings | Nodeglow'; }, []);
  const toast = useToastStore();
  const qc = useQueryClient();

  /* ---- Tab, kept in ?tab= so every tab is linkable (audit F-25) ---- */
  const [activeTab, setActiveTab] = useState<Tab>('system');
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('tab');
    if (t && (TAB_IDS as string[]).includes(t)) setActiveTab(t as Tab);
  }, []);
  const selectTab = useCallback((t: Tab) => {
    setActiveTab(t);
    const url = new URL(window.location.href);
    url.searchParams.set('tab', t);
    window.history.replaceState(window.history.state, '', url);
  }, []);

  /* ---- Server copy + per-section forms ---- */
  const settingsQuery = useQuery<SettingsData>({
    queryKey: ['settings'],
    queryFn: () => get('/settings/json'),
  });
  const settings = settingsQuery.data;
  const at = settingsQuery.dataUpdatedAt;

  const general = useSectionForm(settings, at, generalFromSettings, generalEquals);
  const notif = useSectionForm(settings, at, notifFromSettings);
  const digest = useSectionForm(settings, at, digestFromSettings);
  const ldap = useSectionForm(settings, at, ldapFromSettings);

  const generalSave = useSaveStatus(general.dirty);
  const notifSave = useSaveStatus(notif.dirty);
  const digestSave = useSaveStatus(digest.dirty);

  const anyDirty = general.dirty || notif.dirty || digest.dirty || ldap.dirty;
  useEffect(() => {
    if (!anyDirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [anyDirty]);

  const notifHistory = useQuery<NotifLog[]>({
    queryKey: ['notification-history'],
    queryFn: () => get('/settings/notifications/history'),
    enabled: activeTab === 'notifications',
    refetchInterval: activeTab === 'notifications' ? 30_000 : false,
  });

  /* ---- Mutations (endpoints and bodies unchanged) ---- */

  const saveSettingsMut = useMutation({
    mutationFn: (params: URLSearchParams) => api('/settings/save', { method: 'POST', body: params }),
    onMutate: () => generalSave.start(),
    onSuccess: () => {
      general.markSaved();
      generalSave.succeed();
      qc.invalidateQueries({ queryKey: ['settings'] });
      toast.show('Settings saved', 'success');
    },
    onError: (err) => {
      generalSave.fail(apiErrorMessage(err, 'request failed'));
      toast.show('Failed to save settings', 'error');
    },
  });

  const saveNotifMut = useMutation({
    mutationFn: (params: URLSearchParams) => api('/settings/notifications/save', { method: 'POST', body: params }),
    onMutate: () => notifSave.start(),
    onSuccess: () => {
      notif.markSaved();
      notifSave.succeed();
      qc.invalidateQueries({ queryKey: ['settings'] });
      toast.show('Notification settings saved', 'success');
    },
    onError: (err) => {
      const detail = notifErrorMessage(err);
      notifSave.fail(detail || 'request failed');
      toast.show(detail || 'Failed to save notification settings', 'error');
    },
  });

  const saveDigestMut = useMutation({
    mutationFn: (body: ReturnType<typeof buildDigestBody>) => post('/settings/digest/save', body),
    onMutate: () => digestSave.start(),
    onSuccess: () => {
      digest.markSaved();
      digestSave.succeed();
      qc.invalidateQueries({ queryKey: ['settings'] });
      toast.show('Digest settings saved', 'success');
    },
    onError: (err) => {
      digestSave.fail(apiErrorMessage(err, 'request failed'));
      toast.show('Failed to save digest settings', 'error');
    },
  });

  const [testingChannel, setTestingChannel] = useState<string | null>(null);
  const testNotifMut = useMutation({
    mutationFn: (channel: string) => post<{ ok: boolean; message: string }>('/settings/notifications/test', { channel }),
    onSuccess: (data) => {
      toast.show(data.message || 'Test sent', 'success');
      setTestingChannel(null);
      qc.invalidateQueries({ queryKey: ['notification-history'] });
    },
    onError: (err) => {
      const detail = notifErrorMessage(err);
      toast.show(detail ? `Test notification failed: ${detail}` : 'Test notification failed', 'error');
      setTestingChannel(null);
      qc.invalidateQueries({ queryKey: ['notification-history'] });
    },
  });

  function handleTestChannel(channel: string) {
    if (!notif.value) return;
    setTestingChannel(channel);
    // Auto-save notification settings before testing so the DB has current values
    saveNotifMut.mutate(buildNotificationParams(notif.value), {
      onSuccess: () => testNotifMut.mutate(channel),
      onError: () => setTestingChannel(null),
    });
  }

  const invalidateSettings = useCallback(() => qc.invalidateQueries({ queryKey: ['settings'] }), [qc]);

  /* ---- Render ---- */

  const tabs: TabItem<Tab>[] = TAB_IDS.map((id) => ({
    id,
    label: (
      <>
        {TAB_LABELS[id]}
        {((id === 'system' || id === 'monitoring') && general.dirty)
          || (id === 'notifications' && (notif.dirty || digest.dirty))
          || (id === 'auth' && ldap.dirty) ? (
            <>
              <span aria-hidden="true" className="ml-1.5 inline-block h-[6px] w-[6px] rounded-full bg-degraded" />
              <span className="sr-only"> (unsaved changes)</span>
            </>
          ) : null}
      </>
    ),
  }));

  /** Tabs that need the settings payload share one loading/error branch. */
  const withSettings = (render: (s: SettingsData) => ReactNode) => (
    <QueryState
      query={settingsQuery}
      loading={<FormSkeleton />}
      isEmpty={() => !general.value || !notif.value || !digest.value || !ldap.value}
      empty={<FormSkeleton />}
      errorTitle="Could not load settings"
    >
      {render}
    </QueryState>
  );

  return (
    <div>
      <PageHeader title="Settings" description="Instance-wide configuration. Changes apply to all users." />

      <Tabs<Tab> items={tabs} value={activeTab} onChange={selectTab} label="Settings sections" idBase="settings" className="mb-5" />

      {TAB_IDS.map((id) => (
        <TabPanel key={id} idBase="settings" id={id} active={activeTab === id}>
          {id === 'system' && withSettings(() => general.value && (
            <SystemTab
              form={general.value}
              set={general.set}
              status={generalSave.status}
              onSave={() => general.value && saveSettingsMut.mutate(buildGeneralParams(general.value))}
              onDiscard={general.discard}
            />
          ))}
          {id === 'monitoring' && withSettings(() => general.value && (
            <MonitoringTab
              form={general.value}
              set={general.set}
              status={generalSave.status}
              onSave={() => general.value && saveSettingsMut.mutate(buildGeneralParams(general.value))}
              onDiscard={general.discard}
            />
          ))}
          {id === 'notifications' && withSettings((s) => notif.value && digest.value && (
            <NotificationsTab
              settings={s}
              form={notif.value}
              set={notif.set}
              status={notifSave.status}
              onSave={() => notif.value && saveNotifMut.mutate(buildNotificationParams(notif.value))}
              onDiscard={notif.discard}
              testingChannel={testingChannel}
              onTest={handleTestChannel}
              digest={digest.value}
              setDigest={digest.set}
              digestStatus={digestSave.status}
              onSaveDigest={() => digest.value && saveDigestMut.mutate(buildDigestBody(digest.value))}
              onDiscardDigest={digest.discard}
              history={notifHistory}
            />
          ))}
          {id === 'appearance' && <AppearanceTab />}
          {id === 'api' && <ApiTab />}
          {id === 'ai' && <AiSettingsTab />}
          {id === 'auth' && withSettings((s) => ldap.value && (
            <AuthTab settings={s} ldap={{ ...ldap, value: ldap.value }} onSaved={invalidateSettings} />
          ))}
          {id === 'backup' && <BackupTab />}
          {id === 'license' && <LicenseTab />}
        </TabPanel>
      ))}
    </div>
  );
}
