'use client';

import { StatusPill } from '@/components/ui/StatusPill';
import { Tooltip } from '@/components/ui/Tooltip';
import { cn } from '@/lib/utils';
import {
  HOST_STATE_LABEL, hostStatusProp, observedText, reasonText, type HostState,
} from './hostState';

interface HostStatePillProps {
  state: HostState;
  reason?: string | null;
  observedAt?: string | null;
  size?: 'sm' | 'md';
  /** Wrap the pill in a focusable trigger with the reason as tooltip. Off
   *  where the reason is printed next to it, or inside a link. */
  tooltip?: boolean;
  className?: string;
}

/**
 * Unified host state as a pill: colour, dot shape and text. Disabled is
 * dimmed; unknown has the hollow dot and reads "No data", never green.
 */
export function HostStatePill({ state, reason, observedAt, size = 'md', tooltip = true, className }: HostStatePillProps) {
  const pill = (
    <StatusPill status={hostStatusProp(state)} size={size} className={cn(state === 'disabled' && 'opacity-70', className)}>
      {HOST_STATE_LABEL[state]}
    </StatusPill>
  );
  if (!tooltip) return pill;
  return (
    <Tooltip
      side="right"
      className="w-max max-w-[300px] whitespace-normal"
      content={
        <span className="block">
          <span className="block">{reasonText(state, reason)}</span>
          <span className="mt-0.5 block font-normal opacity-80">{observedText(state, observedAt)}</span>
        </span>
      }
    >
      <button type="button" className="inline-flex rounded-pill" aria-label={`State: ${HOST_STATE_LABEL[state]}`}>
        {pill}
      </button>
    </Tooltip>
  );
}
