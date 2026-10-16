import { ToolCallCard } from "@/components/chat/ToolCallCard";
import { cn } from "@/lib/utils";
import type { Message } from "@/types/chat";

export function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const isThinking = message.status === "streaming" && !message.content;

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div className="flex max-w-[85%] flex-col gap-2">
        {message.toolCalls.map((toolCall, i) => (
          <ToolCallCard key={`${toolCall.name}-${i}`} toolCall={toolCall} />
        ))}

        {(message.content || isThinking) && (
          <div
            className={cn(
              "whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed",
              isUser
                ? "bg-blue-600 text-white"
                : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100",
              message.status === "error" && "ring-2 ring-red-500",
            )}
          >
            {isThinking ? <span className="animate-pulse">…</span> : message.content}
          </div>
        )}
      </div>
    </div>
  );
}
