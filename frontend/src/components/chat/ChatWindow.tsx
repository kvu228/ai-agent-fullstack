import { ChatInput } from "@/components/chat/ChatInput";
import { MessageList } from "@/components/chat/MessageList";
import type { Message } from "@/types/chat";

interface ChatWindowProps {
  messages: Message[];
  isStreaming: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onStop: () => void;
}

/** Presentational: state lives in useChat (see ChatPage). */
export function ChatWindow({ messages, isStreaming, error, onSend, onStop }: ChatWindowProps) {
  return (
    <section className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col px-4 pb-4">
      <MessageList messages={messages} />

      {error && (
        <p
          role="alert"
          className="mb-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
        >
          {error}
        </p>
      )}

      <ChatInput onSend={onSend} onStop={onStop} isStreaming={isStreaming} />
    </section>
  );
}
