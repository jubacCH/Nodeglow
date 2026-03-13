/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxy API requests to the backend during development
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://10.10.30.52:8000/api/:path*',
      },
      {
        source: '/hosts/api/:path*',
        destination: 'http://10.10.30.52:8000/hosts/api/:path*',
      },
      {
        source: '/syslog/api/:path*',
        destination: 'http://10.10.30.52:8000/syslog/api/:path*',
      },
      {
        source: '/syslog/stream',
        destination: 'http://10.10.30.52:8000/syslog/stream',
      },
      {
        source: '/login',
        destination: 'http://10.10.30.52:8000/login',
      },
      {
        source: '/logout',
        destination: 'http://10.10.30.52:8000/logout',
      },
      {
        source: '/health',
        destination: 'http://10.10.30.52:8000/health',
      },
      {
        source: '/ws/:path*',
        destination: 'http://10.10.30.52:8000/ws/:path*',
      },
    ];
  },
};

export default nextConfig;
