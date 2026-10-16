import { getReadiness } from "@/lib/api/health";
import { cn } from "@/lib/utils";

/**
 * Async Server Component: runs on the server, calls the backend directly
 * (BACKEND_URL), ships zero JavaScript to the browser. Render it inside <Suspense>.
 */
export async function BackendStatus() {
  const readiness = await getReadiness();
  const ok = readiness?.status === "ok";
  const down = readiness
    ? Object.entries(readiness.checks)
        .filter(([, healthy]) => !healthy)
        .map(([name]) => name)
    : [];

  return (
    <span
      className="flex items-center gap-2 text-xs text-zinc-500"
      title={JSON.stringify(readiness?.checks ?? {})}
    >
      <span className={cn("size-2 rounded-full", ok ? "bg-green-500" : "bg-red-500")} aria-hidden />
      {!readiness ? "API offline" : ok ? "API ready" : `Degraded: ${down.join(", ")}`}
    </span>
  );
}

export function BackendStatusFallback() {
  return <span className="text-xs text-zinc-400">Checking API…</span>;
}
