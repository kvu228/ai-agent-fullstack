"use client";

import { ChatWindow } from "@/components/chat/ChatWindow";
import { ConversationSidebar } from "@/components/chat/ConversationSidebar";
import { useChat } from "@/hooks/useChat";
import { useConversations } from "@/hooks/useConversations";

/** Client boundary of the chat screen: wires the sidebar and the chat together. */
export function ChatPage() {
  const { conversations, refresh, remove } = useConversations();
  // After each answer the list is refreshed: a new conversation appears / moves to the top.
  const chat = useChat({ onTurnFinished: refresh });

  async function handleDelete(id: string) {
    await remove(id);
    if (id === chat.conversationId) chat.reset();
  }

  return (
    <>
      <ConversationSidebar
        conversations={conversations}
        activeId={chat.conversationId}
        onSelect={(id) => void chat.load(id)}
        onNew={chat.reset}
        onDelete={(id) => void handleDelete(id)}
      />
      <ChatWindow
        messages={chat.messages}
        isStreaming={chat.isStreaming}
        error={chat.error}
        onSend={chat.sendMessage}
        onStop={chat.stop}
      />
    </>
  );
}
