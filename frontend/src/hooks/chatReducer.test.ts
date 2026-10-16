import { describe, expect, it } from "vitest";

import { chatReducer, initialChatState, type ChatState } from "@/hooks/chatReducer";
import type { StreamEvent } from "@/types/api";
import type { Message } from "@/types/chat";

const user: Message = {
  id: "u1",
  role: "user",
  content: "Who leads Atlas?",
  toolCalls: [],
  status: "done",
};
const assistant: Message = {
  id: "a1",
  role: "assistant",
  content: "",
  toolCalls: [],
  status: "streaming",
};

function run(events: StreamEvent[]): ChatState {
  let state = chatReducer(initialChatState, {
    type: "send",
    userMessage: user,
    assistantMessage: assistant,
  });
  for (const event of events) {
    state = chatReducer(state, { type: "stream_event", messageId: "a1", event });
  }
  return state;
}

describe("chatReducer", () => {
  it("builds the assistant message from a full stream", () => {
    const state = run([
      { type: "metadata", conversation_id: "c1" },
      { type: "token", content: "Let me search." },
      { type: "tool_start", tool_name: "search_documents", tool_input: { query: "Atlas lead" } },
      { type: "tool_end", tool_name: "search_documents", content: "[atlas.txt] Minh Tran" },
      { type: "token", content: "The lead " },
      { type: "token", content: "is Minh Tran" },
      { type: "done", conversation_id: "c1" },
    ]);

    expect(state.conversationId).toBe("c1");
    expect(state.isStreaming).toBe(false);
    expect(state.messages[1]).toMatchObject({
      content: "Let me search.\n\nThe lead is Minh Tran",
      status: "done",
      toolCalls: [
        {
          name: "search_documents",
          input: { query: "Atlas lead" },
          output: "[atlas.txt] Minh Tran",
        },
      ],
    });
  });

  it("marks the message as failed on an error event", () => {
    const state = run([{ type: "error", content: "boom" }]);
    expect(state.error).toBe("boom");
    expect(state.messages[1].status).toBe("error");
  });

  it("keeps the error status if finish arrives afterwards", () => {
    let state = run([{ type: "error", content: "boom" }]);
    state = chatReducer(state, { type: "finish", messageId: "a1" });
    expect(state.messages[1].status).toBe("error");
  });

  it("loads a conversation from history", () => {
    const state = chatReducer(initialChatState, {
      type: "load",
      conversationId: "c1",
      messages: [
        { role: "user", content: "Who leads Atlas?" },
        { role: "assistant", content: "Let me search." }, // text before the tool call
        { role: "assistant", content: "Minh Tran." }, // final answer, same turn
        { role: "user", content: "Thanks" },
      ],
    });
    expect(state.conversationId).toBe("c1");
    expect(state.messages.map((m) => [m.role, m.content, m.status])).toEqual([
      ["user", "Who leads Atlas?", "done"],
      ["assistant", "Let me search.\n\nMinh Tran.", "done"],
      ["user", "Thanks", "done"],
    ]);
    expect(new Set(state.messages.map((m) => m.id)).size).toBe(3); // unique React keys
  });

  it("shows an error and an empty chat when loading fails", () => {
    const state = chatReducer(run([]), { type: "load_failed", error: "Not found" });
    expect(state).toMatchObject({ messages: [], conversationId: null, error: "Not found" });
  });
});
