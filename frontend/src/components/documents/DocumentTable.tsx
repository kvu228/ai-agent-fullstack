"use client";

import { getDownloadUrl } from "@/lib/api/documents";
import { cn, formatBytes } from "@/lib/utils";
import type { DocumentInfo, DocumentStatus } from "@/types/api";

const STATUS_STYLE: Record<DocumentStatus, string> = {
  processing: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  ready: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

interface DocumentTableProps {
  documents: DocumentInfo[] | null;
  onDelete: (id: string) => Promise<void>;
}

export function DocumentTable({ documents, onDelete }: DocumentTableProps) {
  if (documents === null) return <p className="text-sm text-zinc-500">Đang tải…</p>;
  if (documents.length === 0) return <p className="text-sm text-zinc-500">Chưa có tài liệu nào.</p>;

  async function download(id: string) {
    // Ask the API for a short-lived presigned URL, then let the browser fetch from S3.
    const { url } = await getDownloadUrl(id);
    window.open(url, "_blank", "noopener");
  }

  return (
    <table className="w-full text-left text-sm">
      <thead className="border-b border-zinc-200 text-xs text-zinc-500 uppercase dark:border-zinc-800">
        <tr>
          <th className="py-2 font-medium">Tên file</th>
          <th className="py-2 font-medium">Kích thước</th>
          <th className="py-2 font-medium">Trạng thái</th>
          <th className="py-2 font-medium">Chunks</th>
          <th className="py-2" />
        </tr>
      </thead>
      <tbody>
        {documents.map((d) => (
          <tr key={d.id} className="border-b border-zinc-100 dark:border-zinc-900">
            <td className="max-w-xs truncate py-2" title={d.filename}>
              {d.filename}
            </td>
            <td className="py-2 text-zinc-500">{formatBytes(d.size_bytes)}</td>
            <td className="py-2">
              <span
                className={cn("rounded-full px-2 py-0.5 text-xs", STATUS_STYLE[d.status])}
                title={d.error ?? undefined}
              >
                {d.status}
              </span>
            </td>
            <td className="py-2 text-zinc-500">{d.status === "ready" ? d.chunk_count : "—"}</td>
            <td className="space-x-3 py-2 text-right whitespace-nowrap">
              <button
                type="button"
                onClick={() => void download(d.id)}
                className="text-blue-600 hover:underline"
              >
                Tải về
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Xoá "${d.filename}"?`)) void onDelete(d.id);
                }}
                className="text-red-600 hover:underline"
              >
                Xoá
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
