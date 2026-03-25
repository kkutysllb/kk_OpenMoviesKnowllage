import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "【kk 量化】FinReport2Video",
  description: "将金融研报自动生成视频",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-slate-950 text-slate-100">{children}</body>
    </html>
  );
}
