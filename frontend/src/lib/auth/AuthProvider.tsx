"use client";

import { useRouter } from "next/navigation";
import { createContext, use, useCallback, useEffect, useMemo, useState } from "react";

import { getMe, logout as logoutRequest } from "@/lib/api/auth";
import { onSessionExpired } from "@/lib/api/client";
import type { User } from "@/types/api";

interface AuthContextValue {
  /** null while loading. The proxy already guarantees a session cookie exists. */
  user: User | null;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Loads the current user once and shares it with every client component below.
 *
 * Tokens are httpOnly cookies: JavaScript can't read them, and doesn't need to.
 * "Am I logged in?" is answered by asking the backend (`GET /auth/me`).
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  const goToLogin = useCallback(() => {
    setUser(null);
    router.replace("/login");
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => !cancelled && setUser(me))
      .catch(() => !cancelled && goToLogin());
    // Any API call that fails even after a refresh ends the session.
    const unsubscribe = onSessionExpired(goToLogin);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [goToLogin]);

  const logout = useCallback(async () => {
    await logoutRequest().catch(() => undefined); // log out locally even if the API is down
    goToLogin();
  }, [goToLogin]);

  const value = useMemo(() => ({ user, logout }), [user, logout]);
  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthContextValue {
  const context = use(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
