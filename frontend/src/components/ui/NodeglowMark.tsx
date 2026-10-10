import { useId } from 'react';
import { cn } from '@/lib/utils';

interface MarkProps {
  /** Pixel size (square). Works from 16 to 64; tested at 20 and 40. */
  size?: number;
  /** Single colour (currentColor) for monochrome contexts. */
  mono?: boolean;
  /** Accessible name; omit when the mark sits next to the wordmark. */
  title?: string;
  className?: string;
}

/**
 * The Nodeglow mark (E3 `#ng-mark`, viewBox 32): a node (r 4.6) in the
 * accent, a soft halo (r 10.5), and a thin orbit (r 11.5) with a 40° gap
 * around a satellite (r 2.15) at −45°. Orbit and satellite use currentColor.
 */
export function NodeglowMark({ size = 30, mono, title, className }: MarkProps) {
  const gid = useId().replace(/:/g, '');
  const accent = 'var(--ng-accent)';
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className={cn('block shrink-0 overflow-visible', className)}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      {!mono && (
        <defs>
          <radialGradient id={`ng-halo-${gid}`} cx="16" cy="16" r="10.5" gradientUnits="userSpaceOnUse">
            <stop offset="0" style={{ stopColor: accent, stopOpacity: 1 }} />
            <stop offset="0.45" style={{ stopColor: accent, stopOpacity: 0.45 }} />
            <stop offset="1" style={{ stopColor: accent, stopOpacity: 0 }} />
          </radialGradient>
        </defs>
      )}
      <circle
        cx="16"
        cy="16"
        r="10.5"
        style={mono ? { fill: 'currentColor', opacity: 0.16 } : { fill: `url(#ng-halo-${gid})`, opacity: 'var(--ng-mark-halo)' }}
      />
      <path d="M26.42 11.14A11.5 11.5 0 1 1 20.86 5.58" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <circle cx="24.13" cy="7.87" r="2.15" fill="currentColor" />
      <circle cx="16" cy="16" r="4.6" style={{ fill: mono ? 'currentColor' : accent }} />
    </svg>
  );
}

/** "Nodeglow" in Sora 600; the o of "glow" carries the accent (and glows in dark mode). */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn('whitespace-nowrap font-display text-[17px] font-semibold leading-none tracking-[-0.035em] text-fg', className)}>
      Nodegl<span className="text-accent [text-shadow:var(--ng-wm-glow)]">o</span>w
    </span>
  );
}

/** Mark + wordmark. */
export function Lockup({ size = 28, className, wordmarkClassName }: { size?: number; className?: string; wordmarkClassName?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2 text-fg', className)}>
      <NodeglowMark size={size} />
      <Wordmark className={wordmarkClassName} />
    </span>
  );
}
