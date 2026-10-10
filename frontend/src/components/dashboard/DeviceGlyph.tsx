import type { ReactNode } from 'react';
import type { Glyph } from '@/lib/dashboard';

/** Device glyphs of the E3 prototype (24px grid, stroke = currentColor). */
const PATHS: Record<Glyph, ReactNode> = {
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z" />
    </>
  ),
  firewall: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 10h18M3 14.5h18M9 5v5M15 10v4.5M9 14.5V19" />
    </>
  ),
  switch: (
    <>
      <rect x="2.5" y="7.5" width="19" height="9" rx="2" />
      <path d="M6 12.5v-1.5M9 12.5v-1.5M12 12.5v-1.5M15 12.5v-1.5M18 12.5v-1.5" />
    </>
  ),
  ap: (
    <>
      <circle cx="12" cy="13" r="7.5" />
      <circle cx="12" cy="13" r="1.3" fill="currentColor" />
      <path d="M8.5 6.2A9 9 0 0 1 12 5.5a9 9 0 0 1 3.5.7" opacity=".55" />
    </>
  ),
  server: (
    <>
      <rect x="4" y="3.5" width="16" height="7" rx="1.6" />
      <rect x="4" y="13.5" width="16" height="7" rx="1.6" />
      <path d="M8 7h.01M8 17h.01M12 7h5M12 17h5" />
    </>
  ),
  hyper: (
    <>
      <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3z" />
      <path d="m3 12 9 4.5 9-4.5M3 16.5 12 21l9-4.5" />
    </>
  ),
  nas: (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M8.5 7h7M8.5 11h7M8.5 15h7" />
      <circle cx="12" cy="18.3" r=".4" fill="currentColor" />
    </>
  ),
  printer: (
    <>
      <path d="M7 9V3.5h10V9" />
      <rect x="3" y="9" width="18" height="8" rx="2" />
      <path d="M7 14h10v6.5H7z" />
    </>
  ),
  probe: (
    <>
      <rect x="4" y="13" width="16" height="7" rx="2" />
      <path d="M12 13V9" />
      <path d="M8.5 7.5a5 5 0 0 1 7 0M6 5a8.5 8.5 0 0 1 12 0" />
      <path d="M8 16.5h.01" />
    </>
  ),
  cloud: <path d="M7 18.5a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 9a4.5 4.5 0 0 1-.5 9.5H7z" />,
  vm: (
    <>
      <rect x="3.5" y="4.5" width="17" height="12" rx="2" />
      <path d="M8 20h8" />
      <path d="M9 9.5l3 1.7 3-1.7M12 11.2v3" />
    </>
  ),
};

export function DeviceGlyph({ glyph, size = 20, className }: { glyph: Glyph; size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      {PATHS[glyph]}
    </svg>
  );
}
