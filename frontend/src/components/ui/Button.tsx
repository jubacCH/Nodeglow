'use client';

import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

const variants: Record<ButtonVariant, string> = {
  // Accent glow only here (hover/focus), per the E3 glow rules.
  primary:
    'bg-accent-btn border-accent-btn text-on-accent hover:bg-accent-btn-hover hover:border-accent-btn-hover hover:shadow-accent-glow focus-visible:shadow-accent-glow',
  secondary: 'bg-surface-2 border-border-2 text-fg hover:bg-surface-3',
  ghost: 'bg-transparent border-transparent text-fg-2 hover:bg-surface-2 hover:text-fg',
  danger: 'bg-down-soft border-down/40 text-down hover:border-down/80',
};

const sizes: Record<ButtonSize, string> = {
  sm: 'h-[28px] px-[10px] text-meta gap-1.5 rounded-ng-sm',
  md: 'h-[36px] px-[14px] text-ui gap-2 rounded-ctl',
  lg: 'h-[40px] px-[16px] text-body gap-2 rounded-ctl',
};

const iconSizes: Record<ButtonSize, string> = {
  sm: 'h-[28px] w-[28px] rounded-ng-sm',
  md: 'h-[36px] w-[36px] rounded-ctl',
  lg: 'h-[40px] w-[40px] rounded-ctl',
};

/** Class string for elements that should look like a button (e.g. <Link>). */
export function buttonClasses({
  variant = 'secondary',
  size = 'md',
  iconOnly = false,
  className,
}: { variant?: ButtonVariant; size?: ButtonSize; iconOnly?: boolean; className?: string } = {}) {
  return cn(
    'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap border font-medium',
    'transition-[background-color,border-color,color,box-shadow] duration-150 ease-out',
    'disabled:cursor-not-allowed disabled:opacity-60 aria-disabled:cursor-not-allowed aria-disabled:opacity-60',
    variants[variant],
    iconOnly ? iconSizes[size] : sizes[size],
    className,
  );
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner, disables the button and sets aria-busy. */
  loading?: boolean;
}

/**
 * Buttons. One primary action per view; secondary for the rest; ghost in
 * toolbars and dense rows; danger only for destructive actions (it is a tinted
 * button, not a solid red block, so it never competes with an incident).
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', loading, disabled, children, type = 'button', ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={buttonClasses({ variant, size, className })}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && <Loader2 size={size === 'sm' ? 13 : 15} className="animate-spin" aria-hidden="true" />}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: icon-only buttons need an accessible name. */
  'aria-label': string;
  variant?: ButtonVariant;
  size?: ButtonSize;
}

/** Square icon-only button (toolbar, top bar). Defaults to ghost. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ className, variant = 'ghost', size = 'md', type = 'button', ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={buttonClasses({ variant, size, iconOnly: true, className })}
      {...props}
    />
  ),
);
IconButton.displayName = 'IconButton';
