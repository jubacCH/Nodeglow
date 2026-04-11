'use client';

import { Component, type ReactNode } from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';

interface Props {
  children: ReactNode;
  /** Widget label shown in the fallback. Purely cosmetic. */
  label?: string;
}

interface State {
  error: Error | null;
}

/**
 * Per-widget React error boundary so a single broken widget doesn't
 * whitescreen the whole dashboard. Renders a small in-place fallback
 * card and offers a reset button to re-mount the children.
 */
export class WidgetErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    if (typeof console !== 'undefined') {
      console.error('[WidgetErrorBoundary]', this.props.label ?? 'widget', error, info);
    }
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div className="h-full min-h-[120px] flex flex-col items-center justify-center gap-2 p-4 text-center">
          <AlertTriangle size={18} className="text-amber-400" />
          <p className="text-xs text-slate-300">
            {this.props.label ? `${this.props.label} failed to render` : 'Widget failed to render'}
          </p>
          <p className="text-[10px] font-mono text-slate-500 max-w-full truncate">
            {this.state.error.message}
          </p>
          <button
            onClick={this.reset}
            className="mt-1 inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] rounded-md border border-white/[0.12] text-slate-300 hover:bg-white/[0.06] hover:text-white transition-colors"
          >
            <RotateCcw size={11} /> Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
