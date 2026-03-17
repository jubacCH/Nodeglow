'use client';

import { useEffect, useRef, useMemo } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { useTopology } from '@/hooks/queries/useTopology';
import { useThemeStore } from '@/stores/theme';
import * as echarts from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { TooltipComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Network, Wifi, WifiOff } from 'lucide-react';

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer]);

export default function TopologyPage() {
  useEffect(() => { document.title = 'Topology | Nodeglow'; }, []);
  const { data, isLoading } = useTopology();
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);
  const colorMode = useThemeStore((s) => s.colorMode);

  const option = useMemo(() => {
    if (!data) return null;
    const { nodes, edges } = data;

    const categories = [
      { name: 'Online', itemStyle: { color: '#10b981' } },
      { name: 'Offline', itemStyle: { color: '#ef4444' } },
      { name: 'Maintenance', itemStyle: { color: '#f59e0b' } },
    ];

    const graphNodes = nodes.map((n) => ({
      id: String(n.id),
      name: n.name,
      symbolSize: edges.some((e) => e.source === n.id) ? 40 : 28,
      category: n.maintenance ? 2 : n.status === 'up' ? 0 : 1,
      label: { show: true, fontSize: 11 },
      tooltip: { formatter: `<b>${n.name}</b><br/>Host: ${n.hostname}<br/>Type: ${n.check_type}<br/>Source: ${n.source}` },
    }));

    const graphEdges = edges.map((e) => ({
      source: String(e.source),
      target: String(e.target),
    }));

    return {
      tooltip: {},
      legend: [{ data: categories.map((c) => c.name), textStyle: { color: '#94a3b8' } }],
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories,
        data: graphNodes,
        links: graphEdges,
        force: {
          repulsion: 300,
          edgeLength: [80, 200],
          gravity: 0.1,
        },
        lineStyle: { color: '#475569', width: 1.5, curveness: 0.1 },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 3 },
        },
        label: {
          color: colorMode === 'light' ? '#1e293b' : '#e2e8f0',
          position: 'bottom',
          distance: 5,
        },
      }],
    };
  }, [data, colorMode]);

  useEffect(() => {
    if (!chartRef.current || !option) return;
    instanceRef.current?.dispose();
    instanceRef.current = echarts.init(chartRef.current);
    instanceRef.current.setOption(option);
    const obs = new ResizeObserver(() => instanceRef.current?.resize());
    obs.observe(chartRef.current);
    return () => {
      obs.disconnect();
      instanceRef.current?.dispose();
      instanceRef.current = null;
    };
  }, [option]);

  const onlineCount = data?.nodes.filter((n) => n.status === 'up' && !n.maintenance).length ?? 0;
  const offlineCount = data?.nodes.filter((n) => n.status === 'down' && !n.maintenance).length ?? 0;
  const maintCount = data?.nodes.filter((n) => n.maintenance).length ?? 0;

  return (
    <div>
      <PageHeader title="Network Topology" description="Visual map of monitored infrastructure" />

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-bold" style={{ color: 'var(--ng-text-primary)' }}>{data?.nodes.length ?? 0}</p>
          <p className="text-xs text-slate-400">Total Nodes</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-bold text-emerald-400">{onlineCount}</p>
          <p className="text-xs text-slate-400">Online</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-bold text-red-400">{offlineCount}</p>
          <p className="text-xs text-slate-400">Offline</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-bold text-amber-400">{maintCount}</p>
          <p className="text-xs text-slate-400">Maintenance</p>
        </GlassCard>
      </div>

      {/* Graph */}
      <GlassCard className="p-4">
        <div className="flex items-center gap-2 mb-4">
          <Network size={16} className="text-sky-400" />
          <h3 className="text-sm font-medium" style={{ color: 'var(--ng-text-primary)' }}>Topology Graph</h3>
          <span className="text-xs text-slate-500 ml-auto">Drag to rearrange, scroll to zoom</span>
        </div>
        {isLoading ? (
          <Skeleton className="w-full h-[500px]" />
        ) : data?.nodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-[500px] text-slate-500">
            <Network size={48} className="mb-3 opacity-30" />
            <p>No topology data available</p>
            <p className="text-xs mt-1">Add hosts and configure integrations to build the topology</p>
          </div>
        ) : (
          <div ref={chartRef} style={{ width: '100%', height: 500 }} />
        )}
      </GlassCard>

      {/* Node list */}
      {data && data.nodes.length > 0 && (
        <GlassCard className="p-4 mt-4">
          <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--ng-text-primary)' }}>All Nodes</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {data.nodes.map((n) => (
              <a
                key={n.id}
                href={`/hosts/${n.id}`}
                className="flex items-center gap-3 px-3 py-2 rounded-md border border-white/[0.06] hover:bg-white/[0.04] transition-colors"
              >
                {n.status === 'up' ? (
                  <Wifi size={14} className="text-emerald-400 shrink-0" />
                ) : (
                  <WifiOff size={14} className="text-red-400 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-sm truncate" style={{ color: 'var(--ng-text-primary)' }}>{n.name}</p>
                  <p className="text-[11px] text-slate-500 truncate">{n.hostname}</p>
                </div>
                {n.maintenance && <Badge variant="severity" severity="warning">Maint</Badge>}
                <Badge>{n.source}</Badge>
              </a>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
}
