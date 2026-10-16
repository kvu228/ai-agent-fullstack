"use client";

import { useCallback, useEffect, useState } from "react";

import { deleteDocument, listDocuments, uploadDocument } from "@/lib/api/documents";
import type { DocumentInfo } from "@/types/api";

const POLL_INTERVAL_MS = 2000;

/**
 * Document list + actions.
 *
 * Ingestion is asynchronous (upload returns 202 "processing"), so while at least
 * one document is still processing we poll the list until it becomes ready/failed.
 */
export function useDocuments() {
  const [documents, setDocuments] = useState<DocumentInfo[] | null>(null); // null = loading
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không tải được danh sách");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const isProcessing = documents?.some((d) => d.status === "processing") ?? false;
  useEffect(() => {
    if (!isProcessing) return;
    const id = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isProcessing, refresh]);

  const upload = useCallback(async (file: File) => {
    setError(null);
    try {
      const created = await uploadDocument(file);
      setDocuments((list) => [created, ...(list ?? [])]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Tải lên thất bại");
    }
  }, []);

  const remove = useCallback(async (id: string) => {
    setError(null);
    try {
      await deleteDocument(id);
      setDocuments((list) => list?.filter((d) => d.id !== id) ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Xoá thất bại");
    }
  }, []);

  return { documents, error, upload, remove };
}
