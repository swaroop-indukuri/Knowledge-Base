"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { listSources, type Source } from "@/lib/api";
import { categoryKey, deriveCategories, isStale, matchesQuery } from "@/lib/sources";
import ChatPanel from "./chat-panel";
import { ChatIcon } from "./icons";
import SaveBar from "./save-bar";
import SourceCard, { SourceCardSkeleton } from "./source-card";
import SourceModal from "./source-modal";

const POLL_MS = 2500;

const pill = (active: boolean) =>
  `rounded-full border px-3 py-1 text-sm transition-colors ${
    active
      ? "border-blue-600 bg-blue-600 text-white"
      : "border-gray-300 bg-white text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
  }`;

export default function Dashboard() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [category, setCategory] = useState<string | null>(null); // categoryKey, null = all
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false); // mobile slide-over only

  const load = useCallback(async () => {
    try {
      setSources(await listSources());
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Couldn't load your sources");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Poll GET /sources while anything is still processing; stops once all are ready/failed.
  const hasProcessing = sources.some((s) => s.status === "processing" && !isStale(s));
  useEffect(() => {
    if (!hasProcessing) return;
    const timer = setInterval(() => {
      listSources().then(setSources).catch(() => {});
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [hasProcessing]);

  const addSource = useCallback((s: Source) => {
    setSources((prev) => (prev.some((p) => p.id === s.id) ? prev : [s, ...prev]));
  }, []);

  const categories = useMemo(() => deriveCategories(sources), [sources]);

  // Drop a selected category once no source has it any more.
  useEffect(() => {
    if (category && !categories.some((c) => c.key === category)) setCategory(null);
  }, [categories, category]);

  const visible = useMemo(
    () =>
      sources.filter(
        (s) => (!category || categoryKey(s.category) === category) && matchesQuery(s, query)
      ),
    [sources, category, query]
  );

  const selected = sources.find((s) => s.id === selectedId) ?? null;
  const closeModal = useCallback(() => setSelectedId(null), []);
  const filtering = category !== null || query.trim() !== "";

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="min-w-0 space-y-6">
        <SaveBar onCreated={addSource} />

        <section aria-label="Saved sources" className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter by category">
              <button className={pill(category === null)} onClick={() => setCategory(null)}>
                All{sources.length > 0 && ` (${sources.length})`}
              </button>
              {categories.map((c) => (
                <button
                  key={c.key}
                  className={pill(category === c.key)}
                  onClick={() => setCategory(category === c.key ? null : c.key)}
                  aria-pressed={category === c.key}
                >
                  {c.label} ({c.count})
                </button>
              ))}
            </div>
            {sources.length > 0 && (
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter sources…"
                aria-label="Filter sources"
                className="input !w-48"
              />
            )}
          </div>

          {loading ? (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }, (_, i) => (
                <SourceCardSkeleton key={i} />
              ))}
            </div>
          ) : loadError ? (
            <div role="alert" className="alert-error flex items-center justify-between gap-3">
              <span>{loadError}</span>
              <button onClick={load} className="font-medium underline">
                Retry
              </button>
            </div>
          ) : sources.length === 0 ? (
            <div className="card flex flex-col items-center gap-1 px-6 py-14 text-center">
              <p className="font-medium">Nothing saved yet</p>
              <p className="max-w-sm text-sm text-gray-500 dark:text-gray-400">
                Paste an article or YouTube link, or drop a PDF above. Once it&apos;s processed you can
                search it and ask questions about it.
              </p>
            </div>
          ) : visible.length === 0 ? (
            <div className="card flex flex-col items-center gap-2 px-6 py-12 text-center">
              <p className="font-medium">No sources match</p>
              {filtering && (
                <button
                  onClick={() => {
                    setCategory(null);
                    setQuery("");
                  }}
                  className="text-sm text-blue-600 hover:underline dark:text-blue-400"
                >
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {visible.map((s) => (
                <li key={s.id} className="flex">
                  <SourceCard source={s} onOpen={() => setSelectedId(s.id)} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* Chat: sticky sidebar on large screens, slide-over drawer below that. */}
      {chatOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={() => setChatOpen(false)}
          aria-hidden="true"
        />
      )}
      <aside
        className={`fixed inset-y-0 right-0 z-40 w-full max-w-md p-3 transition-transform duration-200 lg:sticky lg:top-4 lg:z-auto lg:h-[calc(100vh-6.5rem)] lg:max-w-none lg:translate-x-0 lg:self-start lg:p-0 ${
          chatOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <ChatPanel onClose={() => setChatOpen(false)} />
      </aside>

      <button
        onClick={() => setChatOpen(true)}
        className="btn-primary fixed bottom-4 right-4 z-20 rounded-full px-4 py-3 shadow-lg lg:hidden"
        aria-label="Open chat"
      >
        <ChatIcon className="h-5 w-5" /> Chat
      </button>

      {selected && <SourceModal source={selected} onClose={closeModal} />}
    </div>
  );
}
