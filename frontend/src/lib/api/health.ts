import { API_V1 } from "@/lib/config";
import type { ReadinessResponse } from "@/types/api";

/**
 * Server-only (used by a Server Component): calls the backend directly.
 * Returns null instead of throwing, so the UI can render an "offline" state.
 * A 503 still has a JSON body telling which dependency is down.
 */
export async function getReadiness(): Promise<ReadinessResponse | null> {
  try {
    const response = await fetch(`${API_V1}/health/ready`, { cache: "no-store" });
    return (await response.json()) as ReadinessResponse;
  } catch {
    return null;
  }
}
