import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://a-wk4.vercel.app"),
  title: "金継ぎジャーナル — 失敗の中に金を見つける",
  description: "失敗や挫折をAIが分析し、隠れた知恵と強さを発見するジャーナルアプリ。日本の金継ぎ哲学にインスパイアされた、あなたの成長の記録。",
  openGraph: {
    title: "金継ぎジャーナル — 失敗の中に金を見つける",
    description: "失敗や挫折をAIが分析し、隠れた知恵と強さを発見するジャーナルアプリ。",
    url: "https://a-wk4.vercel.app",
    siteName: "金継ぎジャーナル",
    locale: "ja_JP",
    type: "website",
    images: [
      {
        url: "https://a-wk4.vercel.app/og.png",
        width: 1200,
        height: 630,
        alt: "金継ぎジャーナル",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "金継ぎジャーナル — 失敗の中に金を見つける",
    description: "失敗や挫折をAIが分析し、隠れた知恵と強さを発見するジャーナルアプリ。",
    images: ["https://a-wk4.vercel.app/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja" className="h-full">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
