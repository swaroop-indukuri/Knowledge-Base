"use client";

import { useRef, useState } from "react";
import { MAX_UPLOAD_BYTES, createUrlSource, uploadPdf, type Source } from "@/lib/api";
import { Spinner, UploadIcon } from "./icons";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");

export default function SaveBar({ onCreated }: { onCreated: (source: Source) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<null | "url" | "pdf">(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function saveUrl(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim() || busy) return;
    setError(null);
    setBusy("url");
    try {
      onCreated(await createUrlSource(url.trim()));
      setUrl("");
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(null);
    }
  }

  async function savePdf(file: File | undefined) {
    if (!file || busy) return;
    setError(null);
    if (!file.name.toLowerCase().endsWith(".pdf")) return setError("Only PDF files are supported.");
    if (file.size > MAX_UPLOAD_BYTES) return setError("That file is too large (max 20 MB).");
    setBusy("pdf");
    try {
      onCreated(await uploadPdf(file));
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <section aria-label="Save a source">
      <div className="grid gap-3 md:grid-cols-[1fr_17rem]">
        <form onSubmit={saveUrl} className="card flex flex-col justify-center gap-2 p-4">
          <label htmlFor="source-url" className="text-sm font-medium">
            Save an article or YouTube video
          </label>
          <div className="flex gap-2">
            <input
              id="source-url"
              type="url"
              required
              placeholder="Paste a link…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="input"
            />
            <button type="submit" disabled={busy !== null || !url.trim()} className="btn-primary shrink-0">
              {busy === "url" ? <Spinner /> : null}
              Save
            </button>
          </div>
        </form>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            savePdf(e.dataTransfer.files[0]);
          }}
          className={`flex flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed p-4 text-center text-sm transition-colors ${
            dragging
              ? "border-blue-500 bg-blue-50 dark:bg-blue-950"
              : "border-gray-300 dark:border-gray-700"
          }`}
        >
          {busy === "pdf" ? <Spinner className="h-5 w-5" /> : <UploadIcon className="h-5 w-5 text-gray-500" />}
          <span className="text-gray-600 dark:text-gray-400">
            {busy === "pdf" ? "Uploading…" : "Drop a PDF here (max 20 MB)"}
          </span>
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,application/pdf"
            className="hidden"
            onChange={(e) => savePdf(e.target.files?.[0])}
          />
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => fileInput.current?.click()}
            className="text-blue-600 hover:underline disabled:opacity-50 dark:text-blue-400"
          >
            or choose a file
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="alert-error mt-3">
          {error}
        </p>
      )}
    </section>
  );
}
