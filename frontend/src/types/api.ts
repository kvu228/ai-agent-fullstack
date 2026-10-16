/**
 * The API contract. Mirrors the Pydantic schemas in `backend/app/schemas/`.
 * Keep both sides in sync when you change one of them.
 */

// ---- auth.py --------------------------------------------------------------------

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  created_at: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  full_name?: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

// ---- document.py ----------------------------------------------------------------

export type DocumentStatus = "processing" | "ready" | "failed";

export interface DocumentInfo {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: DocumentStatus;
  chunk_count: number;
  error: string | null;
  created_at: string;
}

export interface DownloadUrlResponse {
  url: string;
  expires_in: number;
}

// ---- chat.py --------------------------------------------------------------------

export interface ChatRequest {
  message: string;
  /** Omit to start a new conversation. */
  conversation_id?: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ConversationMessages {
  conversation_id: string;
  messages: ChatMessage[];
}

/** Server-Sent Events emitted by `POST /api/v1/chat/stream`. */
export type StreamEvent =
  | { type: "metadata"; conversation_id: string }
  | { type: "token"; content: string }
  | { type: "tool_start"; tool_name: string; tool_input: Record<string, unknown> }
  | { type: "tool_end"; tool_name: string; content: string }
  | { type: "done"; conversation_id: string }
  | { type: "error"; content: string };

// ---- health.py ------------------------------------------------------------------

export interface ReadinessResponse {
  status: "ok" | "degraded";
  checks: Record<string, boolean>;
}
