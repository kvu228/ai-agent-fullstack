import type { Metadata } from "next";

import { DocumentsPanel } from "@/components/documents/DocumentsPanel";

export const metadata: Metadata = { title: "Tài liệu · Document Assistant" };

export default function DocumentsPage() {
  return (
    <main className="mx-auto w-full max-w-4xl overflow-y-auto px-4 py-6">
      <h1 className="mb-1 text-lg font-semibold">Tài liệu của bạn</h1>
      <p className="mb-6 text-sm text-zinc-500">
        Tải lên PDF, TXT hoặc Markdown. Sau khi xử lý xong (trạng thái “ready”), trợ lý có thể trả
        lời câu hỏi dựa trên nội dung tài liệu.
      </p>
      <DocumentsPanel />
    </main>
  );
}
