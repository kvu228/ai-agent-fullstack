import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, onSessionExpired } from "@/lib/api/client";

function json(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), { status });
}

/** Replace global fetch with a fake that answers by URL. */
function mockFetch(handler: (url: string) => Response) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => handler(String(input)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch", () => {
  it("refreshes once and retries after a 401", async () => {
    let calls = 0;
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/auth/refresh")) return json(200);
      return ++calls === 1 ? json(401) : json(200, { ok: true });
    });

    const response = await apiFetch("/documents");

    expect(await response.json()).toEqual({ ok: true });
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringMatching(/\/documents$/),
      expect.stringMatching(/\/auth\/refresh$/),
      expect.stringMatching(/\/documents$/),
    ]);
  });

  it("shares a single refresh between parallel requests", async () => {
    const seen = new Set<string>();
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/auth/refresh")) return json(200);
      // First call of each path fails, the retry succeeds.
      if (seen.has(url)) return json(200);
      seen.add(url);
      return json(401);
    });

    await Promise.all([apiFetch("/a"), apiFetch("/b"), apiFetch("/c")]);

    const refreshCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/refresh"));
    expect(refreshCalls).toHaveLength(1);
  });

  it("notifies listeners when the session cannot be refreshed", async () => {
    mockFetch(() => json(401, { error: { message: "Invalid or expired access token" } }));
    const listener = vi.fn();
    const unsubscribe = onSessionExpired(listener);

    await expect(apiFetch("/auth/me")).rejects.toEqual(
      new ApiError("Invalid or expired access token", 401),
    );
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it("does not refresh when told not to (login with a wrong password)", async () => {
    const fetchMock = mockFetch(() => json(401, { error: { message: "Bad credentials" } }));

    await expect(apiFetch("/auth/login", { retryOnUnauthorized: false })).rejects.toThrow(
      "Bad credentials",
    );
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("reads FastAPI validation errors", async () => {
    mockFetch(() => json(422, { detail: [{ loc: ["body", "email"], msg: "Invalid email" }] }));
    await expect(apiFetch("/auth/register")).rejects.toThrow("Invalid email");
  });
});
