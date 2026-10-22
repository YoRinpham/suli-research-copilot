import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "溯理 SULI",
  description: "以可追溯证据为基础的科研工作区",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
