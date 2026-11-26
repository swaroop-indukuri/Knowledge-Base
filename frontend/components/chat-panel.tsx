"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createConversation,
  getMessages,
  streamAsk,
  type Citation,
} from "@/lib/api";
import CitationLink from "./citation-link";
import { CloseIcon } from "./icons";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  error?: string;
  streaming?: boolean;
}

const STORAGE_KEY = "kb.conversation";
const CITE = /(\[\d+(?:\s*,\s*\d+)*\])/g;

function readStoredId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
function storeId(id: string | null) {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage unavailable (private mode); chat still works for this page view
  }
}

/** Text with each [n] turned into a link to the cited source (once citations are known). */
function MessageText({ text, citations }: { text: string; citations: Citation[] }) {
  return (
    <>
      {text.split(CITE).map((part, i) => {
        // split() with a capture group puts the matched "[n]" tokens at odd indexes.
        if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>;
        const nums = part
          .slice(1, -1)
          .split(",")
          .map((s) => parseInt(s, 10));
        return (
          <Fragment key={i}>
            {nums.map((n) => {
              const c = citations.find((x) => x.n === n);
              return c ? (
                <CitationLink key={n} citation={c} compact>
                  [{n}]
                </CitationLink>
              ) : (
                <span key={n}>[{n}]</span>
              );
            })}
          </Fragment>
        );
      })}
    </>
  );
}

function HistorySkeleton() {
  return (
    <div className="animate-pulse space-y-4" aria-hidden="true">
      <div className="ml-auto h-8 w-1/2 rounded bg-gray-200 dark:bg-gray-800" />
      <div className="h-16 w-4/5 rounded bg-gray-200 dark:bg-gray-800" />
    </div>
  );
}

export default function ChatPanel({ onClose }: { onClose?: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Restore the last conversation.
  useEffect(() => {
    const id = readStoredId();
    if (!id) {
      setLoadingHistory(false);
      return;
    }
    getMessages(id)
      .then((history) => {
        setConversationId(id);
        setMessages(
          history.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content ?? "",
            citations: m.citations ?? [],
          }))
        );
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) storeId(null); // stale id
      })
      .finally(() => setLoadingHistory(false));
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const patchLast = (patch: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? patch(m) : m)));

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const question = input.trim();
    if (!question || busy) return;

    setInput("");
    setBusy(true);
    const stamp = Date.now().toString();
    setMessages((prev) => [
      ...prev,
      { id: `u-${stamp}`, role: "user", content: question, citations: [] },
      { id: `a-${stamp}`, role: "assistant", content: "", citations: [], streaming: true },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamAsk(
        { question, conversation_id: conversationId },
        (event) => {
          if (event.type === "meta") {
            setConversationId(event.conversation_id);
            storeId(event.conversation_id);
          } else if (event.type === "delta") {
            patchLast((m) => ({ ...m, content: m.content + event.text }));
          } else if (event.type === "done") {
            patchLast((m) => ({ ...m, id: event.message_id, citations: event.citations, streaming: false }));
          } else if (event.type === "error") {
            patchLast((m) => ({ ...m, error: event.message, streaming: false }));
          }
        },
        controller.signal
      );
      patchLast((m) => ({ ...m, streaming: false }));
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      const message = err instanceof Error ? err.message : "Something went wrong";
      if (err instanceof ApiError && err.status === 404) {
        storeId(null);
        setConversationId(null);
      }
      patchLast((m) => ({ ...m, error: message, streaming: false }));
    } finally {
      setBusy(false);
    }
  }

  async function newChat() {
    abortRef.current?.abort();
    setBusy(false);
    setMessages([]);
    storeId(null);
    try {
      const c = await createConversation();
      setConversationId(c.id);
      storeId(c.id);
    } catch {
      setConversationId(null); // the first question will create one
    }
  }

  return (
    <section className="card flex h-full min-h-0 flex-col" aria-label="Chat with your sources">
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-800">
        <h2 className="font-semibold">Ask your sources</h2>
        <div className="flex items-center gap-3">
          <button onClick={newChat} className="text-sm text-blue-600 hover:underline dark:text-blue-400">
            New chat
          </button>
          {onClose && (
            <button
              onClick={onClose}
              aria-label="Close chat"
              className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 lg:hidden"
            >
              <CloseIcon className="h-5 w-5" />
            </button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4" aria-live="polite">
        {loadingHistory ? (
          <HistorySkeleton />
        ) : messages.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Ask a question about the articles, PDFs and videos you&apos;ve saved. Answers cite their
            sources, and each citation opens the original.
          </p>
        ) : (
          <ul className="space-y-4">
            {messages.map((m) => (
              <li key={m.id} className={m.role === "user" ? "flex justify-end" : ""}>
                <div
                  className={`max-w-[92%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm leading-relaxed ${
                    m.role === "user"
                      ? "bg-blue-600 text-white"
                      : "bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100"
                  }`}
                >
                  {m.role === "assistant" ? (
                    <>
                      {m.content ? (
                        <MessageText text={m.content} citations={m.citations} />
                      ) : (
                        m.streaming && <span className="text-gray-500 dark:text-gray-400">Thinking…</span>
                      )}
                      {m.streaming && m.content && <span className="ml-0.5 animate-pulse">▍</span>}
                      {m.error && (
                        <p role="alert" className="alert-error mt-2">
                          {m.error}
                        </p>
                      )}
                      {m.citations.length > 0 && (
                        <ul className="mt-3 space-y-1 border-t border-gray-300 pt-2 text-xs dark:border-gray-700">
                          {m.citations.map((c) => (
                            <li key={c.n}>
                              <span className="mr-1 text-gray-500 dark:text-gray-400">[{c.n}]</span>
                              <CitationLink citation={c} />
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  ) : (
                    m.content
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={send} className="flex gap-2 border-t border-gray-200 p-3 dark:border-gray-800">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          maxLength={2000}
          placeholder="Ask a question…"
          aria-label="Your question"
          className="input"
        />
        <button type="submit" disabled={busy || !input.trim()} className="btn-primary shrink-0">
          {busy ? "…" : "Ask"}
        </button>
      </form>
    </section>
  );
}
