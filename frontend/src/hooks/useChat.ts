"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { chatReducer, initialChatState } from "@/hooks/chatReducer";
import { getConversationMessages, streamChat } from "@/lib/api/chat";
import type { Message } from "@/types/chat";

function createMessage(role: Message["role"], content: string): Message {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    toolCalls: [],
    status: role === "user" ? "done" : "streaming",
  };
}

interface UseChatOptions {
  /** Called after every answer, e.g. to refresh the conversation list. */
  onTurnFinished?: () => void;
}

/**
 * All chat logic in one hook: components only render `state` and call actions.
 */
export function useChat({ onTurnFinished }: UseChatOptions = {}) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const abortRef = useRef<AbortController | null>(null);
  // Refs (not state) so the callbacks below always see the latest values
  // without being re-created on every change.
  const conversationIdRef = useRef<string | null>(null);
  const onTurnFinishedRef = useRef(onTurnFinished);
  // Bumped whenever the visible conversation changes: a slow `load()` response for
  // a conversation the user already left must not overwrite the current one.
  const viewRef = useRef(0);

  useEffect(() => {
    conversationIdRef.current = state.conversationId;
    onTurnFinishedRef.current = onTurnFinished;
  });

  // Abort any in-flight request when the component unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  /** Drop the in-flight request (if any) so its late events are ignored. */
  const detach = useCallback(() => {
    viewRef.current++;
    const controller = abortRef.current;
    abortRef.current = null; // detach first, so the request is no longer "current"
    controller?.abort();
  }, []);

  const sendMessage = useCallback(async (text: string) => {
    const content = text.trim();
    if (!content || abortRef.current) return;

    viewRef.current++; // the user moved on: ignore a pending load()
    const userMessage = createMessage("user", content);
    const assistantMessage = createMessage("assistant", "");
    dispatch({ type: "send", userMessage, assistantMessage });

    const controller = new AbortController();
    abortRef.current = controller;
    // False once `reset()`/`load()` has dropped this request: its late events must
    // not leak into another conversation.
    const isCurrent = () => abortRef.current === controller;

    try {
      const events = streamChat(
        { message: content, conversation_id: conversationIdRef.current },
        controller.signal,
      );
      for await (const event of events) {
        if (!isCurrent()) return;
        dispatch({ type: "stream_event", messageId: assistantMessage.id, event });
      }
      if (isCurrent()) dispatch({ type: "finish", messageId: assistantMessage.id });
    } catch (error) {
      if (!isCurrent()) return;
      if (controller.signal.aborted) {
        dispatch({ type: "finish", messageId: assistantMessage.id });
      } else {
        const message = error instanceof Error ? error.message : "Something went wrong";
        dispatch({ type: "fail", messageId: assistantMessage.id, error: message });
      }
    } finally {
      if (isCurrent()) abortRef.current = null;
      onTurnFinishedRef.current?.();
    }
  }, []);

  // Stop streaming but keep the partial answer.
  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    detach();
    dispatch({ type: "reset" });
  }, [detach]);

  const load = useCallback(
    async (conversationId: string) => {
      detach();
      const view = viewRef.current;
      try {
        const { messages } = await getConversationMessages(conversationId);
        if (view === viewRef.current) dispatch({ type: "load", conversationId, messages });
      } catch (error) {
        const message = error instanceof Error ? error.message : "Không tải được hội thoại";
        if (view === viewRef.current) dispatch({ type: "load_failed", error: message });
      }
    },
    [detach],
  );

  return { ...state, sendMessage, stop, reset, load };
}
