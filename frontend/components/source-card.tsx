import type { Source } from "@/lib/api";
import { isStale } from "@/lib/sources";
import { SourceTypeIcon, Spinner } from "./icons";

export function SourceCardSkeleton() {
  return (
    <div className="card animate-pulse space-y-3 p-4" aria-hidden="true">
      <div className="flex items-center gap-2">
        <div className="h-5 w-5 rounded bg-gray-200 dark:bg-gray-800" />
        <div className="h-4 w-2/3 rounded bg-gray-200 dark:bg-gray-800" />
      </div>
      <div className="h-5 w-16 rounded-full bg-gray-200 dark:bg-gray-800" />
      <div className="h-3 w-full rounded bg-gray-200 dark:bg-gray-800" />
    </div>
  );
}

export default function SourceCard({ source, onOpen }: { source: Source; onOpen: () => void }) {
  const stale = isStale(source);
  const processing = source.status === "processing" && !stale;
  const title = source.title || source.url || "Untitled";

  return (
    <button
      type="button"
      onClick={onOpen}
      className="card flex w-full flex-col gap-2 p-4 text-left transition-shadow hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
    >
      <div className="flex items-start gap-2">
        <SourceTypeIcon type={source.type} className="mt-0.5 h-5 w-5 shrink-0 text-gray-500 dark:text-gray-400" />
        <h3 className="line-clamp-2 min-w-0 flex-1 font-medium leading-snug">{title}</h3>
      </div>

      {source.status === "ready" && (
        <>
          {source.category && (
            <span className="w-fit rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300">
              {source.category}
            </span>
          )}
          {source.summary && (
            <p className="truncate text-sm text-gray-600 dark:text-gray-400">{source.summary}</p>
          )}
        </>
      )}

      {processing && (
        <p className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          <Spinner /> Processing…
        </p>
      )}

      {stale && <p className="text-sm text-gray-500 dark:text-gray-400">Processing seems stalled.</p>}

      {source.status === "failed" && (
        <p className="line-clamp-2 text-sm text-red-700 dark:text-red-400">
          <span className="font-medium">Failed:</span> {source.error || "Something went wrong."}
        </p>
      )}
    </button>
  );
}
