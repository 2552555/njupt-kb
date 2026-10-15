import type { Metadata, Viewport } from 'next';
import './globals.css';
import { Providers } from './providers';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: '南邮课程知识平台',
    template: '%s · 南邮课程知识平台',
  },
  description:
    '南京邮电大学课程资料与学习内容平台：按课程、学期与类型查找可追溯的学习资料。',
  applicationName: '南邮课程知识平台',
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#59AAA5',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full bg-canvas text-text-primary">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
