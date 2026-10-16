/**
 * Where to send API requests.
 *
 * - In the browser: same-origin `/api/v1`. next.config.ts rewrites `/api/*` to the
 *   backend, so the auth cookies are first-party and there is no CORS to configure.
 * - On the server (Server Components): straight to the backend via `BACKEND_URL`.
 *
 * `BACKEND_URL` has no `NEXT_PUBLIC_` prefix, so it never ends up in the browser bundle.
 */
export const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export const API_V1 = typeof window === "undefined" ? `${BACKEND_URL}/api/v1` : "/api/v1";
