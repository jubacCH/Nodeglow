'use client';

import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
}

const variants = {
  primary: 'bg-sky-500/20 text-sky-400 border-sky-500/30 hover:bg-sky-500/30',
  ghost: 'bg-transparent text-slate-300 border-white/[0.06] hover:bg-white/[0.06]',
  danger: 'bg-red-500/20 text-red-400 border-red-500/30 hover:bg-red-500/30',
};

const sizes = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-2.5 text-sm',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-md border font-medium transition-all',
        'focus:outline-none focus:ring-2 focus:ring-sky-500/50',
        'disabled:opacity-50 disabled:pointer-events-none',
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled}
      {...props}
    >
      {children}
    </button>
  ),
);
Button.displayName = 'Button';
