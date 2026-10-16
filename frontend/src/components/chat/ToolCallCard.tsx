import type { ToolCall } from "@/types/chat";

export function ToolCallCard({ toolCall }: { toolCall: ToolCall }) {
  const pending = toolCall.output === undefined;

  return (
    <details className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900">
      {/* Retrieved passages can be long: collapsed by default, click to inspect. */}
      <summary className="flex cursor-pointer items-center gap-2 text-zinc-500">
        <span aria-hidden>{pending ? "⏳" : "🔎"}</span>
        <span className="font-semibold text-zinc-700 dark:text-zinc-300">{toolCall.name}</span>
        <span className="truncate">({JSON.stringify(toolCall.input)})</span>
      </summary>
      {!pending && (
        <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap text-zinc-600 dark:text-zinc-400">
          {toolCall.output}
        </pre>
      )}
    </details>
  );
}
