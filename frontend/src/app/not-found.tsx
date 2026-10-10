import Link from 'next/link';

export const metadata = { title: 'Not found' };

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="max-w-md text-center">
        <p className="num font-display text-num-lg font-medium tracking-[-0.045em] text-accent">404</p>
        <h1 className="mt-4 text-lead font-semibold text-fg">Page not found</h1>
        <p className="mt-2 text-ui text-fg-3">
          The page you were looking for does not exist or has moved.
        </p>
        {/* Server component: buttonClasses() lives in a client module, so the
            secondary button look is spelled out here. */}
        <Link
          href="/"
          className="mt-6 inline-flex h-[36px] items-center justify-center rounded-ctl border border-border-2 bg-surface-2 px-[14px] text-ui font-medium text-fg transition-colors duration-150 hover:bg-surface-3"
        >
          Back to the dashboard
        </Link>
      </div>
    </main>
  );
}
