import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { getSlateContext } from "@/lib/api";
import { SlateProvider } from "@/lib/slate";
import { TopNav, BottomNav } from "@/components/Nav";
import type { SlateContext } from "@/lib/types";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Orb AI — Home Run Scouting",
  description: "Daily home-run probability predictions, visualized.",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Slate-relative thresholds and stat percentile curves, computed server-side
  // over the entire slate. This used to pull /api/top-picks?n=200 — 254 KB of
  // hitter objects on every page render, capped below the size of a real slate
  // and, once top-picks started gating, drawn from the publishable subset only.
  // Falls back gracefully if the API is down.
  const ctxRes = await getSlateContext();
  const slateContext: SlateContext | null = ctxRes.ok ? ctxRes.data : null;

  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrains.variable} h-full`}
    >
      <body className="min-h-full flex flex-col bg-base text-text-pri">
        <SlateProvider context={slateContext}>
          <TopNav />
          <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-4">{children}</main>
          <BottomNav />
        </SlateProvider>
      </body>
    </html>
  );
}
