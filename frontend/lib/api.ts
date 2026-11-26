import { createClient } from "@/lib/supabase/client";
import { ServerUnavailableError, wakeFetch } from "@/lib/wake";

// Set NEXT_PUBLIC_API_URL to the backend's URL (the Render URL in production). It is
// inlined at build time, so redeploy after changing it. The localhost fallback is for
// local development only.
const CONFIGURED_API_URL = process.env.NEXT_PUBLIC_API_URL;
const API_URL = (CONFIGURED_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const MISCONFIGURED = process.env.NODE_ENV === "production" && !CONFIGURED_API_URL;

function assertConfigured() {
  if (MISCONFIGURED) {
    throw new ApiError(0, "NEXT_PUBLIC_API_URL isn't set for this deployment, so the app can't find its backend.");
  }
}

/** fetch against the backend that tolerates a sleeping free-tier server (see lib/wake.ts). */
async function backendFetch(path: string, init: RequestInit): Promise<Response> {
  assertConfigured();
  try {
    return await wakeFetch(`${API_URL}${path}`, init);
  } catch (e) {
    if (e instanceof ServerUnavailableError) throw new ApiError(0, e.message);
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the server. Is the backend running?");
  }
}

/** Fire-and-forget ping so a sleeping backend starts booting before the user needs it. */
export function warmUpServer(): void {
  if (MISCONFIGURED) return;
  wakeFetch(`${API_URL}/health`, {}, { silent: true }).catch(() => {});
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Response types mirror backend/app/models.py.
export interface MeResponse {
  user_id: string;
}

export type SourceStatus = "processing" | "ready" | "failed";

export interface Source {
  id: string;
  type: "url" | "pdf" | "youtube";
  url: string | null;
  title: string | null;
  summary: string | null;
  category: string | null;
  status: SourceStatus;
  error: string | null;
  created_at: string;
}

export interface FileUrl {
  url: string;
  expires_in: number;
}

/** A [n] citation in an answer, resolved to its real source and location. */
export interface Citation {
  n: number;
  source_id: string;
  chunk_id?: string;
  source_type: "url" | "pdf" | "youtube";
  source_url: string | null;
  title: string | null;
  meta: {
    page?: number;
    timestamp_seconds?: number;
    video_id?: string;
    section?: string;
  };
  snippet?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string | null;
  citations: Citation[];
  created_at: string;
}

export interface Conversation {
  id: string;
  created_at: string;
}

export type AskEvent =
  | { type: "meta"; conversation_id: string }
  | { type: "delta"; text: string }
  | { type: "done"; message_id: string; citations: Citation[] }
  | { type: "error"; message: string };

export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

async function accessToken(): Promise<string | undefined> {
  // getSession() returns the cached session and refreshes the token if it expired.
  const {
    data: { session },
  } = await createClient().auth.getSession();
  return session?.access_token;
}

async function toApiError(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
    else if (Array.isArray(body?.detail)) detail = body.detail[0]?.msg ?? detail; // 422
  } catch {
    // non-JSON error body; keep statusText
  }
  return new ApiError(res.status, detail);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await accessToken();

  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // JSON bodies get a JSON content type; FormData must be left alone so the browser
  // sets the multipart boundary itself.
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await backendFetch(path, { ...init, headers });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

export const getMe = () => api.get<MeResponse>("/me");

export const createUrlSource = (url: string) => api.post<Source>("/sources", { url });

export const uploadPdf = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return api.postForm<Source>("/sources/upload", form);
};

export const listSources = () => api.get<Source[]>("/sources");
export const getSource = (id: string) => api.get<Source>(`/sources/${id}`);
export const getSourceFileUrl = (id: string) => api.get<FileUrl>(`/sources/${id}/file`);

export const createConversation = () => api.post<Conversation>("/conversations");
export const getMessages = (conversationId: string) =>
  api.get<Message[]>(`/conversations/${conversationId}/messages`);

/**
 * POST /ask and read the Server-Sent Events stream. (EventSource can't send POST
 * bodies or auth headers, so the stream is parsed by hand from fetch.)
 */
export async function streamAsk(
  body: { question: string; conversation_id?: string | null },
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const token = await accessToken();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await backendFetch("/ask", { method: "POST", headers, body: JSON.stringify(body), signal });
  if (!res.ok) throw await toApiError(res);
  if (!res.body) throw new ApiError(0, "The server sent an empty response.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (block: string) => {
    let event = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7).trim();
      else if (line.startsWith("data: ")) data += line.slice(6);
    }
    if (!event || !data) return;
    try {
      onEvent({ type: event, ...JSON.parse(data) } as AskEvent);
    } catch {
      // ignore a malformed event rather than killing the stream
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatch(buffer.slice(0, sep));
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}
