import type { ChatMessage, StreamEvent } from "@/types/api";
import type { Message } from "@/types/chat";

/**
 * Pure state machine for the chat UI. Keeping it free of React and `fetch`
 * makes it trivial to unit test (see chatReducer.test.ts).
 */

export interface ChatState {
  messages: Message[];
  /** null until the server assigns one (first `metadata` event). */
  conversationId: string | null;
  isStreaming: boolean;
  error: string | null;
}

export type ChatAction =
  | { type: "send"; userMessage: Message; assistantMessage: Message }
  | { type: "stream_event"; messageId: string; event: StreamEvent }
  | { type: "finish"; messageId: string }
  | { type: "fail"; messageId: string; error: string }
  | { type: "load"; conversationId: string; messages: ChatMessage[] }
  | { type: "load_failed"; error: string }
  | { type: "reset" };

export const initialChatState: ChatState = {
  messages: [],
  conversationId: null,
  isStreaming: false,
  error: null,
};

function updateMessage(
  messages: Message[],
  id: string,
  update: (message: Message) => Message,
): Message[] {
  return messages.map((m) => (m.id === id ? update(m) : m));
}

function applyStreamEvent(state: ChatState, messageId: string, event: StreamEvent): ChatState {
  switch (event.type) {
    case "metadata":
      return { ...state, conversationId: event.conversation_id };
    case "token":
      return {
        ...state,
        messages: updateMessage(state.messages, messageId, (m) => ({
          ...m,
          content: m.content + event.content,
        })),
      };
    case "tool_start":
      return {
        ...state,
        messages: updateMessage(state.messages, messageId, (m) => ({
          ...m,
          // Text before and after a tool call comes from two separate LLM turns:
          // add a paragraph break so they don't run together.
          content: m.content && !m.content.endsWith("\n") ? `${m.content}\n\n` : m.content,
          toolCalls: [...m.toolCalls, { name: event.tool_name, input: event.tool_input }],
        })),
      };
    case "tool_end":
      return {
        ...state,
        messages: updateMessage(state.messages, messageId, (m) => {
          // Attach the output to the first pending call with the same name.
          const index = m.toolCalls.findIndex(
            (t) => t.name === event.tool_name && t.output === undefined,
          );
          if (index === -1) return m;
          const toolCalls = m.toolCalls.map((t, i) =>
            i === index ? { ...t, output: event.content } : t,
          );
          return { ...m, toolCalls };
        }),
      };
    case "error":
      return chatReducer(state, { type: "fail", messageId, error: event.content });
    case "done":
      return chatReducer(state, { type: "finish", messageId });
  }
}

/**
 * Server history -> UI messages.
 *
 * One agent turn can contain several AI messages (text before a tool call, then the
 * final answer). While streaming they end up in ONE bubble, so merge consecutive
 * assistant messages here too: a reloaded conversation looks the same as a live one.
 * History has no tool calls: only the text of each turn is stored for display.
 */
function historyToMessages(conversationId: string, history: ChatMessage[]): Message[] {
  const messages: Message[] = [];
  for (const { role, content } of history) {
    const last = messages.at(-1);
    if (role === "assistant" && last?.role === "assistant") {
      messages[messages.length - 1] = { ...last, content: `${last.content}\n\n${content}` };
      continue;
    }
    messages.push({
      id: `${conversationId}-${messages.length}`,
      role,
      content,
      toolCalls: [],
      status: "done",
    });
  }
  return messages;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "send":
      return {
        ...state,
        messages: [...state.messages, action.userMessage, action.assistantMessage],
        isStreaming: true,
        error: null,
      };
    case "stream_event":
      return applyStreamEvent(state, action.messageId, action.event);
    case "finish":
      return {
        ...state,
        isStreaming: false,
        messages: updateMessage(state.messages, action.messageId, (m) =>
          // Don't let a late "finish" overwrite an earlier "error".
          m.status === "streaming" ? { ...m, status: "done" } : m,
        ),
      };
    case "fail":
      return {
        ...state,
        isStreaming: false,
        error: action.error,
        messages: updateMessage(state.messages, action.messageId, (m) => ({
          ...m,
          status: "error",
        })),
      };
    case "load":
      return {
        ...initialChatState,
        conversationId: action.conversationId,
        messages: historyToMessages(action.conversationId, action.messages),
      };
    case "load_failed":
      return { ...initialChatState, error: action.error };
    case "reset":
      return initialChatState;
  }
}
