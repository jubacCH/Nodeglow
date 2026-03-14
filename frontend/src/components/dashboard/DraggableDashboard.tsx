'use client';

import { useState, useCallback, useMemo, useRef, useEffect, type ReactNode } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Lock, Unlock } from 'lucide-react';

const STORAGE_KEY = 'ng-dashboard-layout';

export interface WidgetDef {
  id: string;
  title?: string;
  defaultLayout: { x: number; y: number; w: number; h: number; minW?: number; minH?: number };
  render: () => ReactNode;
}

interface LayoutItem {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
}

type LayoutMap = Record<string, LayoutItem[]>;

function loadLayouts(): LayoutMap | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveLayouts(layouts: LayoutMap) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layouts));
  } catch { /* ignore */ }
}

interface DraggableDashboardProps {
  widgets: WidgetDef[];
}

export function DraggableDashboard({ widgets }: DraggableDashboardProps) {
  const [locked, setLocked] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null) as React.RefObject<HTMLDivElement>;
  const [RGL, setRGL] = useState<{ ResponsiveGridLayout: React.ComponentType<Record<string, unknown>>; useContainerWidth: (ref: React.RefObject<HTMLDivElement | null>) => { width: number } } | null>(null);

  // Dynamic import to avoid SSR issues
  useEffect(() => {
    import('react-grid-layout').then((mod) => {
      setRGL({
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ResponsiveGridLayout: mod.ResponsiveGridLayout as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        useContainerWidth: mod.useContainerWidth as any,
      });
    });
  }, []);

  const defaultLayouts = useMemo<LayoutMap>(() => {
    const lg = widgets.map((w): LayoutItem => ({
      i: w.id,
      ...w.defaultLayout,
    }));
    // 2-col for medium screens
    const md = widgets.map((w, idx): LayoutItem => ({
      i: w.id,
      x: w.defaultLayout.w >= 3 ? 0 : (idx % 2),
      y: idx * (w.defaultLayout.h || 3),
      w: Math.min(w.defaultLayout.w, 2),
      h: w.defaultLayout.h,
      minW: 1,
      minH: w.defaultLayout.minH,
    }));
    const sm = widgets.map((w, idx): LayoutItem => ({
      i: w.id,
      x: 0,
      y: idx * (w.defaultLayout.h || 3),
      w: 1,
      h: w.defaultLayout.h,
      minW: 1,
      minH: w.defaultLayout.minH,
    }));
    return { lg, md, sm };
  }, [widgets]);

  const [layouts, setLayouts] = useState<LayoutMap>(() => loadLayouts() ?? defaultLayouts);

  const handleLayoutChange = useCallback((_layout: LayoutItem[], allLayouts: LayoutMap) => {
    setLayouts(allLayouts);
    saveLayouts(allLayouts);
  }, []);

  const handleReset = useCallback(() => {
    setLayouts(defaultLayouts);
    saveLayouts(defaultLayouts);
  }, [defaultLayouts]);

  // While loading the grid library, just show a simple grid
  if (!RGL) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {widgets.map((w) => (
          <div key={w.id} className={w.defaultLayout.w >= 3 ? 'lg:col-span-3 md:col-span-2' : w.defaultLayout.w >= 2 ? 'lg:col-span-2 md:col-span-2' : ''}>
            <GlassCard className="p-4">
              {w.render()}
            </GlassCard>
          </div>
        ))}
      </div>
    );
  }

  return <DraggableInner
    RGL={RGL}
    containerRef={containerRef}
    widgets={widgets}
    layouts={layouts}
    locked={locked}
    setLocked={setLocked}
    handleLayoutChange={handleLayoutChange}
    handleReset={handleReset}
  />;
}

function DraggableInner({
  RGL,
  containerRef,
  widgets,
  layouts,
  locked,
  setLocked,
  handleLayoutChange,
  handleReset,
}: {
  RGL: { ResponsiveGridLayout: React.ComponentType<Record<string, unknown>>; useContainerWidth: (ref: React.RefObject<HTMLDivElement | null>) => { width: number } };
  containerRef: React.RefObject<HTMLDivElement | null>;
  widgets: WidgetDef[];
  layouts: LayoutMap;
  locked: boolean;
  setLocked: (fn: (prev: boolean) => boolean) => void;
  handleLayoutChange: (layout: LayoutItem[], allLayouts: LayoutMap) => void;
  handleReset: () => void;
}) {
  const { width } = RGL.useContainerWidth(containerRef);
  const GridLayout = RGL.ResponsiveGridLayout;

  return (
    <div ref={containerRef as React.LegacyRef<HTMLDivElement>}>
      <div className="flex items-center justify-end gap-2 mb-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setLocked((l: boolean) => !l)}
          title={locked ? 'Unlock layout' : 'Lock layout'}
        >
          {locked ? <Lock size={14} /> : <Unlock size={14} />}
          {locked ? 'Locked' : 'Editing'}
        </Button>
        {!locked && (
          <Button size="sm" variant="ghost" onClick={handleReset}>
            Reset Layout
          </Button>
        )}
      </div>
      {width > 0 && (
        <GridLayout
          className="react-grid-layout"
          width={width}
          layouts={layouts}
          breakpoints={{ lg: 1200, md: 768, sm: 0 }}
          cols={{ lg: 3, md: 2, sm: 1 }}
          rowHeight={110}
          isDraggable={!locked}
          isResizable={!locked}
          onLayoutChange={handleLayoutChange}
          draggableHandle=".drag-handle"
          margin={[16, 16]}
          containerPadding={[0, 0]}
        >
          {widgets.map((w) => (
            <div key={w.id}>
              <GlassCard className="h-full overflow-hidden flex flex-col">
                {!locked && (
                  <div className="drag-handle cursor-move px-4 py-1.5 border-b border-white/[0.04] flex items-center">
                    <span className="text-[10px] text-slate-500 uppercase tracking-wider select-none">
                      ⋮⋮ {w.title ?? w.id}
                    </span>
                  </div>
                )}
                <div className="flex-1 overflow-auto p-4">
                  {w.render()}
                </div>
              </GlassCard>
            </div>
          ))}
        </GridLayout>
      )}
    </div>
  );
}
