'use client';

import {
  cloneElement, forwardRef, isValidElement, useId,
  type InputHTMLAttributes, type ReactElement, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Form field wrapper: label, control, hint and error, wired with htmlFor,
 * aria-describedby and aria-invalid (audit F-50). The child control receives
 * `id`, `aria-describedby` and `aria-invalid` automatically.
 *
 *   <Field label="Hostname" hint="FQDN or IP" error={errors.host}>
 *     <Input value={host} onChange={…} />
 *   </Field>
 */
export function Field({
  label, hint, error, required, children, className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: ReactElement<Record<string, unknown>>;
  className?: string;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;
  const control = isValidElement(children)
    ? cloneElement(children, {
        id: (children.props.id as string | undefined) ?? id,
        'aria-describedby': describedBy,
        'aria-invalid': error ? true : undefined,
        required: required ?? children.props.required,
      })
    : children;
  const controlId = (isValidElement(children) && (children.props.id as string | undefined)) || id;
  return (
    <div className={cn('min-w-0', className)}>
      <label htmlFor={controlId} className="ng-label">
        {label}
        {required && <span className="ml-0.5 text-down" aria-hidden="true">*</span>}
      </label>
      {control}
      {hint && !error && <p id={hintId} className="mt-1.5 text-meta text-fg-3">{hint}</p>}
      {error && <p id={errorId} className="mt-1.5 text-meta text-down">{error}</p>}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn('ng-input', className)} {...props} />,
);
Input.displayName = 'Input';

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn('ng-input', className)} {...props}>{children}</select>
  ),
);
Select.displayName = 'Select';

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn('ng-input', className)} {...props} />,
);
Textarea.displayName = 'Textarea';

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: ReactNode;
  description?: ReactNode;
}

/** Native checkbox (keeps all browser semantics) with E3 styling. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, description, className, id, ...props }, ref) => {
    const auto = useId();
    const cid = id ?? auto;
    return (
      <div className={cn('flex items-start gap-2.5', className)}>
        <span className="relative mt-[2px] inline-flex h-4 w-4 shrink-0">
          <input
            ref={ref}
            id={cid}
            type="checkbox"
            className="peer h-4 w-4 cursor-pointer appearance-none rounded-[4px] border border-line bg-surface transition-colors checked:border-accent-btn checked:bg-accent-btn disabled:cursor-not-allowed disabled:opacity-50"
            {...props}
          />
          <Check size={12} strokeWidth={3} aria-hidden="true" className="pointer-events-none absolute left-[2px] top-[2px] text-on-accent opacity-0 peer-checked:opacity-100" />
        </span>
        {(label || description) && (
          <label htmlFor={cid} className="cursor-pointer text-ui text-fg">
            {label}
            {description && <span className="block text-meta text-fg-2">{description}</span>}
          </label>
        )}
      </div>
    );
  },
);
Checkbox.displayName = 'Checkbox';

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Visible label (or pass aria-label). */
  label?: ReactNode;
  description?: ReactNode;
  'aria-label'?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
}

/** On/off switch: a button with role="switch" and aria-checked. Use for
 *  settings that apply immediately; use Checkbox inside forms with Save. */
export function Switch({ checked, onChange, label, description, disabled, id, className, ...rest }: SwitchProps) {
  const auto = useId();
  const sid = id ?? auto;
  const labelId = label ? `${sid}-label` : undefined;
  return (
    <div className={cn('flex items-start gap-3', className)}>
      <button
        id={sid}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-label={labelId ? undefined : rest['aria-label']}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-[1px] inline-flex h-[20px] w-[34px] shrink-0 items-center rounded-pill border transition-colors disabled:cursor-not-allowed disabled:opacity-50',
          checked ? 'border-accent-btn bg-accent-btn' : 'border-line bg-surface-3',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'inline-block h-[14px] w-[14px] rounded-full shadow-ng-sm transition-transform duration-150',
            checked ? 'translate-x-[16px] bg-on-accent' : 'translate-x-[2px] bg-fg-2',
          )}
        />
      </button>
      {(label || description) && (
        <div className="min-w-0">
          {label && (
            <label id={labelId} htmlFor={sid} className="cursor-pointer text-ui text-fg">
              {label}
            </label>
          )}
          {description && <p className="text-meta text-fg-2">{description}</p>}
        </div>
      )}
    </div>
  );
}
