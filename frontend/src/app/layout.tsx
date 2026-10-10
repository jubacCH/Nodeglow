import type { Metadata, Viewport } from 'next';
import { Inter_Tight, JetBrains_Mono, Sora } from 'next/font/google';
import { Providers } from '@/components/Providers';
import { THEME_INIT_SCRIPT } from '@/lib/theme';
import { ngTokensRaw } from '@/styles/tokens.gen';
import './globals.css';

// Fonts per concept E3: Sora for display text and big numbers, Inter Tight
// for UI and body, JetBrains Mono for rule names, logs and addresses. The CSS
// variables feed --ng-font-* in tokens.gen.css (fallback stacks included).
const interTight = Inter_Tight({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-inter-tight',
  display: 'swap',
});

const sora = Sora({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-sora',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
});

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
      className={`${interTight.variable} ${sora.variable} ${jetbrainsMono.variable}`}
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
