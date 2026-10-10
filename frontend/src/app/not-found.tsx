import Link from 'next/link';

export const metadata = { title: 'Not found' };

export default function NotFound() {
  return (
    <main className="min-h-screen flex items-center justify-center px-4" style={{ background: 'var(--ng-bg)' }}>
      <div className="text-center max-w-md">
        <p className="text-5xl font-semibold bg-gradient-to-r from-sky-400 to-violet-400 bg-clip-text text-transparent">404</p>
        <h1 className="mt-4 text-lg font-semibold text-slate-200">Page not found</h1>
        <p className="mt-2 text-sm text-slate-500">
          The page you were looking for does not exist or has moved.
        </p>
        <Link
          href="/"
          className="inline-block mt-6 px-4 py-2 rounded-md text-sm text-slate-200 bg-white/[0.06] border border-white/[0.1] hover:bg-white/[0.1] transition-colors"
        >
          Back to the dashboard
        </Link>
      </div>
    </main>
  );
}
