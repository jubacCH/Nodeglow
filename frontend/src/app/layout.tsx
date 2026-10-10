import type { Metadata, Viewport } from 'next';
import { Providers } from '@/components/Providers';
import { THEME_INIT_SCRIPT } from '@/lib/theme';
import { ngTokensRaw } from '@/styles/tokens.gen';
import { fontVariables } from './fonts';
import './globals.css';

export const metadata: Metadata = {
  title: {
    template: '%s | Nodeglow',
    default: 'Nodeglow',
  },
  description: 'Infrastructure monitoring platform',
  icons: {
    icon: '/favicon.svg',
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: dark)', color: ngTokensRaw.dark.bg },
    { media: '(prefers-color-scheme: light)', color: ngTokensRaw.light.bg },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // data-theme is set before first paint by THEME_INIT_SCRIPT (stored or
    // system theme), hence suppressHydrationWarning on <html> only.
    <html
      lang="en"
      data-theme="dark"
      suppressHydrationWarning
      className={fontVariables}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="bg-bg font-sans text-fg antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
