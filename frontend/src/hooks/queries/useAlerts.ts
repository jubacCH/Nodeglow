import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { apiErrorMessage, get, post } from '@/lib/api';
import type { IncidentItem, IncidentPage } from '@/lib/incidents';
import { useToastStore } from '@/stores/toast';
import type { Incident } from '@/types';

export function useIncidents(status?: string) {
  return useQuery({
    queryKey: ['incidents', status],
    queryFn: () => get<Incident[]>(`/api/v1/incidents${status ? `?status=${status}` : ''}`),
    refetchInterval: 30_000,
  });
}

export const INCIDENT_LIST_KEY = ['incidents', 'list'] as const;

/** One page of the server-filtered incident list (envelope with total). */
export function useIncidentList(queryString: string) {
  return useQuery({
    queryKey: [...INCIDENT_LIST_KEY, queryString],
    queryFn: () => get<IncidentPage>(`/api/v1/incidents?${queryString}`),
    refetchInterval: 30_000,
    // Keep the old page on screen while the next one loads (no flicker).
    placeholderData: keepPreviousData,
  });
}

type IncidentAction = 'acknowledge' | 'resolve';

interface ActionVars {
  id: number;
  action: IncidentAction;
}

function applyAction<T extends Pick<Incident, 'status' | 'resolved_at'> & { acknowledged?: boolean }>(inc: T, action: IncidentAction): T {
  if (action === 'acknowledge') return { ...inc, status: 'acknowledged', acknowledged: true };
  return { ...inc, status: 'resolved', acknowledged: false, resolved_at: inc.resolved_at ?? new Date().toISOString() };
}

/**
 * Acknowledge / resolve with an optimistic update of every cached list page
 * and the detail query. A failed request rolls the caches back and shows an
 * error toast; afterwards the lists, the detail and the shell counters refetch.
 */
export function useIncidentAction() {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);

  return useMutation({
    mutationFn: ({ id, action }: ActionVars) =>
      // acknowledge reads a JSON body (optional "by"); send an empty object.
      post(`/api/v1/incidents/${id}/${action}`, {}),
    onMutate: async ({ id, action }) => {
      await qc.cancelQueries({ queryKey: INCIDENT_LIST_KEY });
      await qc.cancelQueries({ queryKey: ['incident', id] });
      const snapshot: [QueryKey, unknown][] = [
        ...qc.getQueriesData({ queryKey: INCIDENT_LIST_KEY }),
        ...qc.getQueriesData({ queryKey: ['incident', id] }),
      ];
      qc.setQueriesData<IncidentPage>({ queryKey: INCIDENT_LIST_KEY }, (page) =>
        page && { ...page, items: page.items.map((i: IncidentItem) => (i.id === id ? applyAction(i, action) : i)) },
      );
      qc.setQueryData<IncidentItem>(['incident', id], (inc) => inc && applyAction(inc, action));
      return { snapshot };
    },
    onError: (err, { action }, ctx) => {
      ctx?.snapshot.forEach(([key, data]) => qc.setQueryData(key, data));
      toast(apiErrorMessage(err, action === 'acknowledge' ? 'Could not acknowledge the incident' : 'Could not resolve the incident'), 'error');
    },
    onSuccess: (_d, { action }) => {
      toast(action === 'acknowledge' ? 'Incident acknowledged' : 'Incident resolved', 'success');
    },
    onSettled: (_d, _e, { id }) => {
      qc.invalidateQueries({ queryKey: ['incidents'] });
      qc.invalidateQueries({ queryKey: ['incident', id] });
      qc.invalidateQueries({ queryKey: ['v1-status'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}
