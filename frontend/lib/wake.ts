/**
 * Cold-start handling for a backend on a free tier that sleeps when idle (Render:
 * ~15 min idle -> sleeps; the next request can take 30-50 s while it boots).
 *
 * `wakeFetch` is a drop-in `fetch` that, until the server has answered once (or after a
 * long idle gap), will:
 *   - flip a shared status to "waking" so the UI can show "Waking up the server..."
 *     when a response is slow (> slowMs) or a request fails;
 *   - retry connection failures and gateway errors (502/503/504) with backoff, for up
 *     to `deadlineMs` (default 60 s) in total.
 * Once the server is known to be awake, only idempotent GET/HEAD requests are retried
 * (retrying a POST could create duplicates); other methods fail normally.
 *
 * No imports on purpose, so the logic can be tested in plain Node.
 */

export type ServerStatus = "ok" | "waking";

const DEFAULT_DEADLINE_MS = 60_000;
const DEFAULT_SLOW_MS = 2_500;
const DEFAULT_BACKOFF_MS = [1_000, 2_000, 3_000, 5_000];
const IDLE_SLEEP_MS = 14 * 60_000; // Render sleeps after 15 min; re-arm just before that
const RETRYABLE_STATUS = new Set([502, 503, 504]);

export class ServerUnavailableError extends Error {
  constructor() {
    super("The server didn't respond. It may still be starting up - please try again in a moment.");
    this.name = "ServerUnavailableError";
  }
}

// ---- shared status store (subscribe / snapshot, for useSyncExternalStore) ----
let status: ServerStatus = "ok";
let wakingSince: number | null = null;
let awake = false; // has the backend answered since page load (and not been idle too long)?
let lastOk = 0;
let wakingCount = 0; // requests currently waiting on a (possibly) sleeping server
const listeners = new Set<() => void>();

export const getServerStatus = (): ServerStatus => status;
export const getWakingSince = (): number | null => wakingSince;
export function subscribeServerStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function setStatus(next: ServerStatus) {
  if (next === status) return;
  status = next;
  wakingSince = next === "waking" ? Date.now() : null;
  listeners.forEach((l) => l());
}

/** Test helper: forget everything we know about the server. */
export function resetServerState() {
  status = "ok";
  wakingSince = null;
  awake = false;
  lastOk = 0;
  wakingCount = 0;
  listeners.forEach((l) => l());
}

export interface WakeOptions {
  /** Don't show the "waking" banner (used for background warm-up pings). */
  silent?: boolean;
  deadlineMs?: number;
  slowMs?: number;
  backoffMs?: number[];
  /** Injectable for tests. */
  sleep?: (ms: number) => Promise<void>;
  fetchImpl?: typeof fetch;
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function abortError() {
  return new DOMException("The operation was aborted.", "AbortError");
}

export async function wakeFetch(
  url: string,
  init: RequestInit = {},
  opts: WakeOptions = {}
): Promise<Response> {
  const doFetch = opts.fetchImpl ?? fetch;
  const sleep = opts.sleep ?? realSleep;
  const deadlineMs = opts.deadlineMs ?? DEFAULT_DEADLINE_MS;
  const backoff = opts.backoffMs ?? DEFAULT_BACKOFF_MS;
  const userSignal = init.signal ?? undefined;
  const method = (init.method ?? "GET").toUpperCase();

  // After a long idle gap the server has probably gone back to sleep.
  if (awake && Date.now() - lastOk > IDLE_SLEEP_MS) awake = false;

  const startedAwake = awake;
  const canRetry = !startedAwake || method === "GET" || method === "HEAD";
  const deadline = Date.now() + deadlineMs;
  let counted = false; // this request is currently keeping the banner up
  const show = () => {
    if (opts.silent || counted) return;
    counted = true;
    wakingCount++;
    setStatus("waking");
  };

  // Slow first response (server booting while the proxy holds the request) -> show banner.
  const slowTimer = startedAwake ? undefined : setTimeout(show, opts.slowMs ?? DEFAULT_SLOW_MS);

  try {
    for (let attempt = 0; ; attempt++) {
      if (userSignal?.aborted) throw abortError();

      let response: Response | undefined;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        if (canRetry) {
          // Each attempt may use whatever is left of the overall deadline.
          const controller = new AbortController();
          timer = setTimeout(() => controller.abort(), Math.max(deadline - Date.now(), 1));
          userSignal?.addEventListener("abort", () => controller.abort(), { once: true });
          response = await doFetch(url, { ...init, signal: controller.signal });
        } else {
          response = await doFetch(url, init);
        }
      } catch (err) {
        if (userSignal?.aborted) throw err; // the caller cancelled: don't retry
        if (!canRetry) throw err;
        // connection failure / timeout: fall through to retry
      } finally {
        if (timer) clearTimeout(timer); // the stream (if any) keeps running
      }

      if (response && !(canRetry && RETRYABLE_STATUS.has(response.status))) {
        awake = true;
        lastOk = Date.now();
        return response;
      }

      if (Date.now() >= deadline) {
        if (response) {
          // Gateway error persisted to the deadline: hand it back to the caller.
          return response;
        }
        throw new ServerUnavailableError();
      }

      show();
      awake = false; // a failure means it's not (reliably) up
      const wait = Math.min(backoff[Math.min(attempt, backoff.length - 1)] ?? 1_000, Math.max(deadline - Date.now(), 0));
      await sleep(wait);
    }
  } finally {
    if (slowTimer) clearTimeout(slowTimer);
    // Hide the banner once no request is waiting any more (success or gave up; the
    // caller reports any error).
    if (counted) {
      wakingCount--;
      if (wakingCount === 0) setStatus("ok");
    }
  }
}
