import type { Metadata, Viewport } from 'next';
import { Poppins } from 'next/font/google';
import { brand } from '@qa/brand';
import { Toaster } from '@/components/ui/toaster';
import './globals.css';

const poppins = Poppins({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600', '700'],
  variable: '--font-sans',
  display: 'swap',
});

export const metadata: Metadata = {
  metadataBase: new URL(brand.url),
  title: {
    default: `${brand.name} — ${brand.tagline}`,
    template: `%s · ${brand.name}`,
  },
  description: brand.description,
  applicationName: brand.name,
  openGraph: {
    type: 'website',
    siteName: brand.name,
    title: brand.name,
    description: brand.description,
    url: brand.url,
    images: [brand.ogImagePath],
  },
  twitter: {
    card: 'summary_large_image',
    title: brand.name,
    description: brand.description,
    images: [brand.ogImagePath],
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: brand.themeColor,
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={poppins.variable}>
      {/* suppressHydrationWarning on <body> tolerates browser extensions
          (ColorZilla, Grammarly, etc.) that inject attrs before React hydrates. */}
      <body suppressHydrationWarning className="font-sans antialiased">
        {children}
        <Toaster />
      </body>
    </html>
  );
}
