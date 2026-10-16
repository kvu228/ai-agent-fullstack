"use client";

import { useCallback, useEffect, useState } from "react";

import { deleteConversation, listConversations } from "@/lib/api/chat";
import type { Conversation } from "@/types/api";

/** The sidebar's data: the user's conversations, newest activity first. */
export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);

  const refresh = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch {
      // Non-critical: keep the previous list (an expired session is handled globally).
    }
  }, []);

  useEffect(() => {
    // Initial load: fetching on mount is a legitimate effect (syncing with the server).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await deleteConversation(id);
    setConversations((list) => list.filter((c) => c.id !== id));
  }, []);

  return { conversations, refresh, remove };
}
