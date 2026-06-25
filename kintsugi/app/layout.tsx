import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kintsugi Journal — Find Gold in Your Failures",
  description: "The AI journal that transforms your failures into wisdom. Named after the Japanese art of repairing broken pottery with gold.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
