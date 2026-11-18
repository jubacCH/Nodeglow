'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/Button';
import { Table, TBody, THead, Td, Th, Tr } from '@/components/ui/Table';
import { useConfirm } from '@/hooks/useConfirm';
import { del, get } from '@/lib/api';

interface HostKey {
  id: number;
  host: string;
  port: number;
  key_type: string;
  fingerprint: string;
  created_at: string | null;
  created_by: string | null;
}

/**
 * Host keys confirmed for "Install over SSH". A deploy accepts only the key
 * stored here; after a host was rebuilt, remove its key so the next deploy
 * asks again.
 */
export function SshHostKeysCard() {
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const keys = useQuery({
    queryKey: ['ssh-host-keys'],
    queryFn: () => get<{ keys: HostKey[] }>('/api/ssh-host-keys').then((r) => r.keys),
  });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/ssh-host-keys/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ssh-host-keys'] }),
  });

  if (!keys.data?.length) return null;
  return (
    <Card as="section" className="mt-6">
      <CardHeader title="SSH host keys" />
      <p className="mb-3 text-ui text-fg-2">
        Hosts Nodeglow has installed agents on over SSH. Only the key shown here is accepted. Remove a key after the host
        was rebuilt; the next installation asks you to confirm the new one.
      </p>
      <Table>
        <THead>
          <Tr>
            <Th>Host</Th>
            <Th>Fingerprint</Th>
            <Th>Confirmed by</Th>
            <Th><span className="sr-only">Actions</span></Th>
          </Tr>
        </THead>
        <TBody>
          {keys.data.map((k) => (
            <Tr key={k.id}>
              <Td className="font-code text-meta">{k.port === 22 ? k.host : `${k.host}:${k.port}`}</Td>
              <Td className="break-all font-code text-meta">{k.fingerprint}</Td>
              <Td className="text-meta text-fg-2">{k.created_by ?? '—'}</Td>
              <Td className="text-right">
                <IconButton
                  size="sm"
                  aria-label={`Remove the host key of ${k.host}`}
                  onClick={async () => {
                    const ok = await confirm({
                      title: 'Remove host key',
                      description: `The next installation on ${k.host} will ask you to confirm its host key again.`,
                      confirmLabel: 'Remove',
                      variant: 'danger',
                    });
                    if (ok) remove.mutate(k.id);
                  }}
                >
                  <Trash2 size={14} aria-hidden="true" />
                </IconButton>
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
      {ConfirmDialogElement}
    </Card>
  );
}
