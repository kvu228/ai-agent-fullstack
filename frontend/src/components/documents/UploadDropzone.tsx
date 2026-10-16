"use client";

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";

import { cn } from "@/lib/utils";

// Must match the backend allow-list (app/ingestion/loaders.py). The backend checks
// again: client-side checks are only for a nicer UX, never for security.
const ACCEPT = ".pdf,.txt,.md";

export function UploadDropzone({ onUpload }: { onUpload: (file: File) => Promise<void> }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  async function uploadAll(files: FileList | null) {
    if (!files?.length) return;
    setIsUploading(true);
    try {
      for (const file of Array.from(files)) await onUpload(file);
    } finally {
      setIsUploading(false);
      if (inputRef.current) inputRef.current.value = ""; // allow re-selecting the same file
    }
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    void uploadAll(event.dataTransfer.files);
  }

  return (
    <label
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={cn(
        "flex cursor-pointer flex-col items-center gap-1 rounded-2xl border-2 border-dashed border-zinc-300 px-6 py-8 text-center text-sm dark:border-zinc-700",
        isDragging && "border-blue-500 bg-blue-50 dark:bg-blue-950",
        isUploading && "pointer-events-none opacity-60",
      )}
    >
      <span className="font-medium">
        {isUploading ? "Đang tải lên…" : "Kéo thả file vào đây hoặc bấm để chọn"}
      </span>
      <span className="text-xs text-zinc-500">PDF, TXT, Markdown</span>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        className="sr-only"
        onChange={(e: ChangeEvent<HTMLInputElement>) => void uploadAll(e.target.files)}
      />
    </label>
  );
}
