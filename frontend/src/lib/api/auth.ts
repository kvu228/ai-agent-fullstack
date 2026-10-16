import { apiFetch, apiJson } from "@/lib/api/client";
import type { LoginRequest, RegisterRequest, TokenResponse, User } from "@/types/api";

// The backend sets httpOnly cookies on login: we don't store the token anywhere.
// A 401 from these endpoints means "wrong credentials", not "expired session",
// so they opt out of the refresh-and-retry logic.

export function login(body: LoginRequest): Promise<TokenResponse> {
  return apiJson("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
    retryOnUnauthorized: false,
  });
}

export function register(body: RegisterRequest): Promise<User> {
  return apiJson("/auth/register", {
    method: "POST",
    body: JSON.stringify(body),
    retryOnUnauthorized: false,
  });
}

export async function logout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST", retryOnUnauthorized: false });
}

export function getMe(): Promise<User> {
  return apiJson("/auth/me");
}
