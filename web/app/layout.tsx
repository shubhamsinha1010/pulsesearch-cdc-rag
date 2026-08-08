import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PulseSearch",
  description:
    "Real-time CDC + hybrid search + grounded RAG over a live data firehose.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
