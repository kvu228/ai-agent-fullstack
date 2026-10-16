"use client";

import { useAuth } from "@/lib/auth/AuthProvider";

export function UserMenu() {
  const { user, logout } = useAuth();
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="hidden text-zinc-500 sm:inline">{user?.email ?? "…"}</span>
      <button
        type="button"
        onClick={logout}
        className="rounded-lg border border-zinc-300 px-3 py-1.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
      >
        Đăng xuất
      </button>
    </div>
  );
}
