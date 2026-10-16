import { apiFetch, apiJson } from "@/lib/api/client";
import type { DocumentInfo, DownloadUrlResponse } from "@/types/api";

export function listDocuments(): Promise<DocumentInfo[]> {
  return apiJson("/documents");
}

/** Upload returns immediately (202) with status "processing"; ingestion runs in the background. */
export function uploadDocument(file: File): Promise<DocumentInfo> {
  const form = new FormData();
  form.append("file", file);
  return apiJson("/documents", { method: "POST", body: form });
}

/** A short-lived presigned S3 URL: the browser downloads straight from S3, not via our API. */
export function getDownloadUrl(id: string): Promise<DownloadUrlResponse> {
  return apiJson(`/documents/${id}/download`);
}

export async function deleteDocument(id: string): Promise<void> {
  await apiFetch(`/documents/${id}`, { method: "DELETE" });
}
