import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// Name of the HttpOnly session cookie set by the backend on login.
const SESSION_COOKIE = 'nodeglow_session';

// Public paths that must be reachable without a session.
const PUBLIC_PATHS = ['/login', '/setup', '/install'];

/**
 * Server-side auth guard. Redirects unauthenticated requests for protected
 * routes to /login. Static assets, Next internals and public paths are skipped.
 *
 * Note: this is a coarse presence check on the session cookie — the backend
 * still validates the token on every API call. It exists to prevent the
 * protected app shell from being served to unauthenticated visitors.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public auth/setup routes.
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  const hasSession = Boolean(request.cookies.get(SESSION_COOKIE)?.value);
  if (!hasSession) {
    const loginUrl = new URL('/login', request.url);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Run on everything except Next internals, the API/proxy routes (the backend
  // authenticates those itself) and static asset files.
  matcher: [
    '/((?!_next/static|_next/image|api|hosts/api|syslog|ws|health|system/status|settings|rules|setup|install|agents/download|static|favicon.ico|logo-icon.svg|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|woff2?|ttf)$).*)',
  ],
};
