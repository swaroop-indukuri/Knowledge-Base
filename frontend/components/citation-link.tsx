"use client";

import { useState, type ReactNode } from "react";
import { getSourceFileUrl, type Citation } from "@/lib/api";
import {
  formatTimestamp,
  isVideoId,
  youtubeIdFromUrl,
  youtubeTimestampUrl,
} from "@/lib/youtube";

const linkClass = "text-blue-600 hover:underline dark:text-blue-400";

/**
 * Renders a citation as a link that opens the source at the cited spot:
 *  - YouTube: youtube.com/watch?v=ID&t=142s (jumps to the timestamp)
 *  - PDF: the original via a signed URL, opened at the cited page (#page=N)
 *  - Web article: the page itself
 * `children` replaces the default label (used for inline "[1]" markers); `compact`
 * hides the location badge.
 */
export default function CitationLink({
  citation,
  children,
  compact = false,
}: {
  citation: Citation;
  children?: ReactNode;
  compact?: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const { source_type, source_url, title, meta } = citation;
  const label = children ?? title ?? source_url ?? "Source";
  const page = typeof meta.page === "number" && meta.page > 0 ? meta.page : undefined;

  async function openPdf() {
    setError(null);
    // Open the tab synchronously (popup blockers), then point it at the signed URL.
    const tab = window.open("", "_blank");
    try {
      const { url } = await getSourceFileUrl(citation.source_id);
      if (tab) {
        tab.opener = null;
        tab.location.href = page ? `${url}#page=${page}` : url;
      }
    } catch (e) {
      tab?.close();
      setError(e instanceof Error ? e.message : "Couldn't open the PDF");
    }
  }

  if (source_type === "youtube") {
    const id = isVideoId(meta.video_id) ? meta.video_id : youtubeIdFromUrl(source_url);
    if (id) {
      const seconds = typeof meta.timestamp_seconds === "number" ? meta.timestamp_seconds : undefined;
      return (
        <a
          href={youtubeTimestampUrl(id, seconds)}
          target="_blank"
          rel="noopener noreferrer"
          className={linkClass}
          title={title ? `${title}${seconds ? ` at ${formatTimestamp(seconds)}` : ""}` : undefined}
        >
          {label}
          {!compact && seconds !== undefined && seconds > 0 && (
            <span className="ml-1 whitespace-nowrap text-xs text-gray-500">
              ▶ {formatTimestamp(seconds)}
            </span>
          )}
        </a>
      );
    }
  }

  if (source_type === "pdf") {
    return (
      <>
        <button
          type="button"
          onClick={openPdf}
          className={linkClass}
          title={title ? `${title}${page ? `, page ${page}` : ""}` : undefined}
        >
          {label}
          {!compact && page !== undefined && (
            <span className="ml-1 whitespace-nowrap text-xs text-gray-500">p. {page}</span>
          )}
        </button>
        {error && <span className="ml-2 text-xs text-red-600">{error}</span>}
      </>
    );
  }

  if (source_url) {
    return (
      <a href={source_url} target="_blank" rel="noopener noreferrer" className={linkClass} title={title ?? undefined}>
        {label}
      </a>
    );
  }

  return <span>{label}</span>;
}
