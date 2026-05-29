/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',

  // Proxy API + data routes to backend. In dev: set BACKEND_URL=http://10.10.30.52:8000
  async rewrites() {
    const backend = process.env.BACKEND_URL || 'http://nodeglow:8000';
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/hosts/api/:path*', destination: `${backend}/hosts/api/:path*` },
      { source: '/syslog/api/:path*', destination: `${backend}/syslog/api/:path*` },
      { source: '/syslog/stream', destination: `${backend}/syslog/stream` },
      { source: '/health', destination: `${backend}/health` },
      { source: '/system/status', destination: `${backend}/system/status` },
      { source: '/settings/:path*', destination: `${backend}/settings/:path*` },
      { source: '/rules/:path*', destination: `${backend}/rules/:path*` },
      { source: '/ws/:path*', destination: `${backend}/ws/:path*` },
      { source: '/setup', destination: `${backend}/setup` },
      { source: '/setup/:path*', destination: `${backend}/setup/:path*` },
      { source: '/install/:path*', destination: `${backend}/install/:path*` },
      { source: '/agents/download/:path*', destination: `${backend}/agents/download/:path*` },
      { source: '/static/:path*', destination: `${backend}/static/:path*` },
    ];
  },

  async headers() {
    // The API origin. When NEXT_PUBLIC_API_URL is empty the app talks to the
    // backend through same-origin rewrites, so 'self' already covers it.
    const apiOrigin = process.env.NEXT_PUBLIC_API_URL || '';
    // Allow same-origin WebSocket (ws/wss) for /ws/live and the SSE endpoint.
    // We cannot know the runtime host at build time, so allow ws:/wss: schemes
    // generically for connect-src (still restricted to the WS/SSE use case).
    const connectSrc = ["'self'", 'ws:', 'wss:', apiOrigin].filter(Boolean).join(' ');

    const csp = [
      "default-src 'self'",
      // Next.js App Router injects inline bootstrap/hydration scripts; without a
      // nonce middleware we must allow 'unsafe-inline'. echarts renders to
      // canvas and needs no eval.
      "script-src 'self' 'unsafe-inline'",
      // Tailwind/CSS-in-JS inline styles and CSS custom properties are used
      // extensively across the app.
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      // Self-hosted Geist/Inter fonts served from /_next.
      "font-src 'self' data:",
      `connect-src ${connectSrc}`,
      // Three.js / echarts may use blob workers.
      "worker-src 'self' blob:",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
    ].join('; ');

    return [
      {
        source: '/:path*',
        headers: [
          { key: 'Content-Security-Policy', value: csp },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          // 2 years, include subdomains, allow preload list submission.
          { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
        ],
      },
    ];
  },
};

export default nextConfig;
