import { Skeleton } from '@/components/ui/Skeleton';

/** Shown inside the app shell while a route's code is loading. */
export default function AppLoading() {
  return (
    <div aria-busy="true" aria-label="Loading page">
      <Skeleton className="h-8 w-56 mb-2" />
      <Skeleton className="h-4 w-80 mb-6" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
