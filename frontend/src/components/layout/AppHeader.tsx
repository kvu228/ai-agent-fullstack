import { Suspense } from "react";

import { BackendStatus, BackendStatusFallback } from "@/components/BackendStatus";
import { NavLinks } from "@/components/layout/NavLinks";
import { UserMenu } from "@/components/layout/UserMenu";

/** Server Component that mixes server-only (BackendStatus) and client (NavLinks, UserMenu) parts. */
export function AppHeader() {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
      <div className="flex items-center gap-4">
        <span className="font-semibold">📄 Document Assistant</span>
        <NavLinks />
      </div>
      <div className="flex items-center gap-4">
        {/* Uncached data must stream inside <Suspense> when Cache Components is on. */}
        <Suspense fallback={<BackendStatusFallback />}>
          <BackendStatus />
        </Suspense>
        <UserMenu />
      </div>
    </header>
  );
}
