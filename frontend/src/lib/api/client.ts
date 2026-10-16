import { API_V1 } from "@/lib/config";

/** Error thrown for any non-2xx response, carrying the HTTP status. */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ---- Silent token refresh ---------------------------------------------------------
//
// The access token lives 15 minutes. When a request comes back 401 we call
// /auth/refresh once (the refresh cookie is sent automatically) and retry.
//
// Several requests can fail at the same time (e.g. a page loading 3 lists). They must
// share ONE refresh call: refresh tokens are single-use (rotation), so a second
// parallel refresh would present an already-used token and log the user out.

let refreshInFlight: Promise<boolean> | null = null;

export function refreshSession(): Promise<boolean> {
  refreshInFlight ??= fetch(`${API_V1}/auth/refresh`, { method: "POST" })
    .then((response) => response.ok)
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

// Lets the AuthProvider react (redirect to /login) when the session is really over.
type Listener = () => void;
const sessionExpiredListeners = new Set<Listener>();

export function onSessionExpired(listener: Listener): () => void {
  sessionExpiredListeners.add(listener);
  return () => sessionExpiredListeners.delete(listener);
}

// ---- fetch wrapper ----------------------------------------------------------------

interface ApiFetchOptions extends RequestInit {
  /** Set to false for the auth endpoints themselves (login, refresh, ...). */
  retryOnUnauthorized?: boolean;
}

/** Thin `fetch` wrapper: prefixes the base URL, sets JSON headers, refreshes, normalises errors. */
export async function apiFetch(path: string, options: ApiFetchOptions = {}): Promise<Response> {
  const { retryOnUnauthorized = true, ...init } = options;
  // `new Headers()` accepts every HeadersInit shape (object, array, Headers instance).
  const headers = new Headers(init.headers);
  // FormData (file upload) must set its own multipart Content-Type with the boundary.
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const send = () => fetch(`${API_V1}${path}`, { ...init, headers });

  let response = await send();

  if (response.status === 401 && retryOnUnauthorized) {
    if (await refreshSession()) {
      response = await send();
    }
    if (response.status === 401) {
      sessionExpiredListeners.forEach((listener) => listener());
    }
  }

  if (!response.ok) {
    // The backend returns {"error": {"message": ...}} (or {"detail": [...]} for 422).
    const body = await response.json().catch(() => null);
    throw new ApiError(errorMessage(body) ?? response.statusText, response.status);
  }
  return response;
}

function errorMessage(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const { error, detail } = body as { error?: { message?: unknown }; detail?: unknown };
  if (typeof error?.message === "string") return error.message;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{"loc": [...], "msg": "..."}]
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") return detail[0].msg;
  return null;
}

export async function apiJson<T>(path: string, options?: ApiFetchOptions): Promise<T> {
  const response = await apiFetch(path, options);
  return (await response.json()) as T;
}
