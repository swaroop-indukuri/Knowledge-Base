"use client";

import { useEffect, useRef, useState } from "react";
import { getSourceFileUrl, type Source } from "@/lib/api";
import { isStale, typeLabel } from "@/lib/sources";
import { youtubeIdFromUrl } from "@/lib/youtube";
import { CloseIcon, SourceTypeIcon, Spinner } from "./icons";

export default function SourceModal({ source, onClose }: { source: Source; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  // Escape closes; background scroll is locked while open; focus moves into the dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  async function openOriginalPdf() {
    setFileError(null);
    const tab = window.open("", "_blank"); // open synchronously so popup blockers allow it
    try {
      const { url } = await getSourceFileUrl(source.id);
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      }
    } catch (e) {
      tab?.close();
      setFileError(e instanceof Error ? e.message : "Couldn't open the PDF");
    }
  }

  const videoId = source.type === "youtube" ? youtubeIdFromUrl(source.url) : null;
  const stale = isStale(source);
  const processing = source.status === "processing" && !stale;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-modal-title"
        className="card max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-b-none p-5 shadow-xl sm:rounded-b-lg"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <SourceTypeIcon type={source.type} className="h-4 w-4" />
              {typeLabel[source.type]}
            </p>
            <h2 id="source-modal-title" className="mt-1 text-xl font-semibold leading-snug">
              {source.title || source.url || "Untitled"}
            </h2>
          </div>
          <button
            ref={closeRef}
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            <CloseIcon />
          </button>
        </div>

        {videoId && (
          <div className="mt-4 aspect-video overflow-hidden rounded-md bg-black">
            <iframe
              src={`https://www.youtube-nocookie.com/embed/${videoId}`}
              title={source.title || "YouTube video"}
              allow="accelerometer; encrypted-media; picture-in-picture; fullscreen"
              allowFullScreen
              className="h-full w-full"
            />
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
          {source.status === "ready" && source.category && (
            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300">
              {source.category}
            </span>
          )}
          {processing && (
            <span className="flex items-center gap-1.5 text-gray-500 dark:text-gray-400">
              <Spinner /> Processing…
            </span>
          )}
          <span className="text-gray-500 dark:text-gray-400">
            Saved {new Date(source.created_at).toLocaleDateString(undefined, { dateStyle: "medium" })}
          </span>
        </div>

        <div className="mt-4">
          <h3 className="mb-1 text-sm font-medium">Summary</h3>
          {source.status === "ready" && source.summary ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-700 dark:text-gray-300">
              {source.summary}
            </p>
          ) : source.status === "failed" ? (
            <p role="alert" className="alert-error">
              {source.error || "Processing failed."}
            </p>
          ) : (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {stale ? "Processing seems stalled." : "The summary will appear here once processing finishes."}
            </p>
          )}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          {source.type === "pdf" && (
            <button onClick={openOriginalPdf} className="btn-secondary">
              Open original PDF
            </button>
          )}
          {source.type === "url" && source.url && (
            <a href={source.url} target="_blank" rel="noopener noreferrer" className="btn-secondary">
              Open original page
            </a>
          )}
          {source.type === "youtube" && source.url && (
            <a href={source.url} target="_blank" rel="noopener noreferrer" className="btn-secondary">
              Watch on YouTube
            </a>
          )}
          {fileError && <span className="text-sm text-red-600 dark:text-red-400">{fileError}</span>}
        </div>
      </div>
    </div>
  );
}
