'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import { useIsAdmin } from '@/stores/auth';
import { Plus, Shield, ShieldCheck, Eye } from 'lucide-react';
import { useState } from 'react';

interface UserInfo {
  id: number;
  username: string;
  role: string;
  created_at: string;
}

const roleIcons: Record<string, React.ElementType> = {
  admin: ShieldCheck,
  editor: Shield,
  readonly: Eye,
};

const roleColors: Record<string, string> = {
  admin: 'text-violet-400',
  editor: 'text-sky-400',
  readonly: 'text-slate-400',
};

export default function UsersPage() {
  const isAdmin = useIsAdmin();
  const [showAdd, setShowAdd] = useState(false);

  const { data: users, isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => get<UserInfo[]>('/api/v1/users'),
    enabled: isAdmin,
  });

  if (!isAdmin) {
    return (
      <div>
        <PageHeader title="Users" description="User management" />
        <GlassCard className="p-8 text-center">
          <p className="text-sm text-slate-500">Admin access required</p>
        </GlassCard>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Users"
        description="User management"
        actions={
          <Button size="sm" onClick={() => setShowAdd(true)}>
            <Plus size={16} /> Add User
          </Button>
        }
      />

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Username</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Role</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Created</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.03]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                  </tr>
                ))}
              {users?.map((u) => {
                const RoleIcon = roleIcons[u.role] ?? Eye;
                return (
                  <tr key={u.id} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                    <td className="px-4 py-3 text-slate-200 font-medium">{u.username}</td>
                    <td className="px-4 py-3">
                      <span className={`flex items-center gap-1.5 text-xs ${roleColors[u.role] ?? 'text-slate-400'}`}>
                        <RoleIcon size={14} />
                        {u.role}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {new Date(u.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </GlassCard>

      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add User">
        <p className="text-sm text-slate-400">User creation form coming soon.</p>
      </Modal>
    </div>
  );
}
