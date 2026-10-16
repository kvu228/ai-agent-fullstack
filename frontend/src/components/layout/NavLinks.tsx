"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/documents", label: "Tài liệu" },
] as const;

export function NavLinks() {
  const pathname = usePathname();
  return (
    <nav className="flex gap-1 text-sm">
      {LINKS.map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          aria-current={pathname === href ? "page" : undefined}
          className={cn(
            "rounded-lg px-3 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800",
            pathname === href && "bg-zinc-100 font-medium dark:bg-zinc-800",
          )}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
