import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { getTopPicks } from "@/lib/api";
import { SlateProvider } from "@/lib/slate";
import { TopNav, BottomNav } from "@/components/Nav";
import type { Hitter } from "@/lib/types";

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
  // Slate-relative thresholds computed ONCE from today's full board (PART 2).
  // n=200 so percentiles are stable. Falls back gracefully if API is down.
  const picksRes = await getTopPicks(undefined, 200);
  const picks: Hitter[] = picksRes.ok ? picksRes.data.picks : [];

  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrains.variable} h-full`}
    >
      <body className="min-h-full flex flex-col bg-base text-text-pri">
        <SlateProvider picks={picks}>
          <TopNav />
          <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-4">{children}</main>
          <BottomNav />
        </SlateProvider>
      </body>
    </html>
  );
}
