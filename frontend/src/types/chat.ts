/** UI model for the chat screen (what components render, not what the API sends). */

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  output?: string;
}

export type MessageStatus = "streaming" | "done" | "error";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  status: MessageStatus;
}
