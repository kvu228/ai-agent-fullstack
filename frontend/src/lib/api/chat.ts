import { apiFetch, apiJson } from "@/lib/api/client";
import { readSseStream } from "@/lib/sse";
import type { ChatRequest, Conversation, ConversationMessages, StreamEvent } from "@/types/api";

/**
 * Call the streaming chat endpoint and yield typed events as they arrive.
 *
 * Usage:
 *   for await (const event of streamChat({ message: "hi" })) { ... }
 */
export async function* streamChat(
  body: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await apiFetch("/chat/stream", {
    method: "POST",
    body: JSON.stringify(body),
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.body) throw new Error("Response has no body to stream");

  for await (const { event, data } of readSseStream(response.body)) {
    // The SSE `event:` name is the discriminant; `data:` holds the JSON payload.
    yield { type: event, ...JSON.parse(data) } as StreamEvent;
  }
}

export function listConversations(): Promise<Conversation[]> {
  return apiJson("/conversations");
}

export function getConversationMessages(id: string): Promise<ConversationMessages> {
  return apiJson(`/conversations/${id}/messages`);
}

export async function deleteConversation(id: string): Promise<void> {
  await apiFetch(`/conversations/${id}`, { method: "DELETE" });
}
