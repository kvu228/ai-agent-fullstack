"use client";

import { DocumentTable } from "@/components/documents/DocumentTable";
import { UploadDropzone } from "@/components/documents/UploadDropzone";
import { useDocuments } from "@/hooks/useDocuments";

export function DocumentsPanel() {
  const { documents, error, upload, remove } = useDocuments();

  return (
    <div className="flex flex-col gap-6">
      <UploadDropzone onUpload={upload} />
      {error && (
        <p
          role="alert"
          className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
        >
          {error}
        </p>
      )}
      <DocumentTable documents={documents} onDelete={remove} />
    </div>
  );
}
