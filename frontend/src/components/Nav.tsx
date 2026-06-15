"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import Logo from "./Logo";

/*
  Navigation (PART 6). Desktop: top nav with links. Mobile: fixed bottom
  tab bar [ Games | Top Picks | Accuracy ]. usePathname drives active state.
*/

const LINKS = [
  { href: "/", label: "Games", match: (p: string) => p === "/" || p.startsWith("/game") || p.startsWith("/matchup") },
  { href: "/top-picks", label: "Top Picks", match: (p: string) => p.startsWith("/top-picks") },
  { href: "/play", label: "Pick'em", match: (p: string) => p.startsWith("/play") },
  { href: "/accuracy", label: "Accuracy", match: (p: string) => p.startsWith("/accuracy") },
];

export function TopNav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-white/5 bg-base/90 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
        <Logo />
        <nav className="hidden items-center gap-1 md:flex">
          {LINKS.map((l) => {
            const active = l.match(pathname);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded-md px-3 py-1.5 font-sans text-sm transition-colors ${
                  active ? "bg-hover text-neon-lime" : "text-text-muted hover:text-text-pri"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

export function BottomNav() {
  const pathname = usePathname();
  return (
    <nav className="sticky bottom-0 z-20 border-t border-white/5 bg-base/95 backdrop-blur md:hidden">
      <div className="mx-auto flex max-w-3xl">
        {LINKS.map((l) => {
          const active = l.match(pathname);
          return (
            <Link
              key={l.href}
              href={l.href}
              className={`flex-1 py-3 text-center font-sans text-xs ${
                active ? "text-neon-lime" : "text-text-muted"
              }`}
            >
              {l.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
