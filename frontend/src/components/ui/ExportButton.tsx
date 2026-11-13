'use client';

import { useState, useRef, useEffect } from 'react';
import { Download } from 'lucide-react';
import { Button } from './Button';

interface ExportButtonProps {
  data: Record<string, unknown>[];
  filename: string;
  columns?: { key: string; label: string }[];
}

/**
 * A cell starting with = + - @ (or a tab / carriage return) is run as a
 * formula by Excel and LibreOffice. Hostnames and syslog messages come from
 * the network, so such a cell gets a leading apostrophe and stays text.
 * Plain numbers keep their sign.
 */
export function csvCell(val: unknown): string {
  if (val === null || val === undefined) return '';
  let str = String(val);
  if (typeof val !== 'number' && /^[=+\-@\t\r]/.test(str)) str = `'${str}`;
  return `"${str.replace(/"/g, '""')}"`;
}

export function toCSV(data: Record<string, unknown>[], columns?: { key: string; label: string }[]): string {
  if (data.length === 0) return '';
  const cols = columns ?? Object.keys(data[0]).map((k) => ({ key: k, label: k }));
  const header = cols.map((c) => csvCell(c.label)).join(',');
  const rows = data.map((row) =>
    cols.map((c) => csvCell(row[c.key])).join(',')
  );
  return [header, ...rows].join('\n');
}

function download(content: string, filename: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function ExportButton({ data, filename, columns }: ExportButtonProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  function exportCSV() {
    download(toCSV(data, columns), `${filename}.csv`, 'text/csv');
    setOpen(false);
  }

  function exportJSON() {
    download(JSON.stringify(data, null, 2), `${filename}.json`, 'application/json');
    setOpen(false);
  }

  return (
    <div className="relative" ref={ref}>
      <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>
        <Download size={16} />
        Export
      </Button>
      {open && (
        <div className="absolute right-0 mt-1 z-popover w-36 overflow-hidden rounded-card border border-border-2 bg-surface p-1 shadow-overlay">
          <button
            onClick={exportCSV}
            className="w-full rounded-ng-sm px-2.5 py-2 text-left text-ui text-fg hover:bg-surface-2 transition-colors"
          >
            CSV
          </button>
          <button
            onClick={exportJSON}
            className="w-full rounded-ng-sm px-2.5 py-2 text-left text-ui text-fg hover:bg-surface-2 transition-colors"
          >
            JSON
          </button>
        </div>
      )}
    </div>
  );
}
