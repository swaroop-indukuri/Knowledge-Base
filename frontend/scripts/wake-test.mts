import {
  wakeFetch,
  resetServerState,
  getServerStatus,
  subscribeServerStatus,
  ServerUnavailableError,
} from "../lib/wake.ts";

let failed = 0;
function check(name: string, cond: boolean, extra = "") {
  if (!cond) failed++;
  console.log(cond ? "PASS" : "FAIL", name, cond ? "" : extra);
}
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const ok = (status = 200) => new Response("{}", { status });

/** fetch mock driven by a script of outcomes: "net" = connection error, number = HTTP status. */
function scripted(outcomes: (string | number)[], delayMs = 0) {
  let calls = 0;
  const impl = (async (_url: string, init: RequestInit = {}) => {
    const outcome = outcomes[Math.min(calls, outcomes.length - 1)];
    calls++;
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(resolve, delayMs);
      init.signal?.addEventListener("abort", () => {
        clearTimeout(t);
        reject(new DOMException("aborted", "AbortError"));
      });
    });
    if (outcome === "net") throw new TypeError("Failed to fetch");
    return ok(outcome as number);
  }) as unknown as typeof fetch;
  return { impl, calls: () => calls };
}

function track() {
  const seen: string[] = [];
  const unsub = subscribeServerStatus(() => seen.push(getServerStatus()));
  return { seen, stop: unsub };
}
const fast = { backoffMs: [5], sleep: wait };

// 1. healthy server: no banner, no retries
{
  resetServerState();
  const s = scripted([200]);
  const t = track();
  const r = await wakeFetch("http://x/health", {}, { ...fast, fetchImpl: s.impl, slowMs: 50 });
  check("healthy: returns response", r.status === 200);
  check("healthy: 1 attempt, banner never shown", s.calls() === 1 && t.seen.length === 0, JSON.stringify(t.seen));
  t.stop();
}

// 2. sleeping server: connection errors, then it wakes up
{
  resetServerState();
  const s = scripted(["net", "net", "net", 200]);
  const t = track();
  const r = await wakeFetch("http://x/sources", {}, { ...fast, fetchImpl: s.impl });
  check("wake: eventually succeeds", r.status === 200);
  check("wake: 4 attempts", s.calls() === 4, String(s.calls()));
  check("wake: banner shown then hidden", t.seen.join(",") === "waking,ok", JSON.stringify(t.seen));
  t.stop();
}

// 3. gateway errors (502/503/504) are retried
{
  resetServerState();
  const s = scripted([503, 502, 504, 200]);
  const r = await wakeFetch("http://x/sources", {}, { ...fast, fetchImpl: s.impl });
  check("gateway errors retried until 200", r.status === 200 && s.calls() === 4, `${r.status}/${s.calls()}`);
}

// 4. real errors are NOT retried
for (const code of [400, 401, 404, 422, 500]) {
  resetServerState();
  const s = scripted([code]);
  const r = await wakeFetch("http://x/sources", {}, { ...fast, fetchImpl: s.impl });
  check(`HTTP ${code} returned immediately, no retry`, r.status === code && s.calls() === 1, String(s.calls()));
}

// 5. slow held response (server booting behind the proxy) shows the banner without failing
{
  resetServerState();
  const s = scripted([200], 150);
  const t = track();
  const p = wakeFetch("http://x/sources", {}, { ...fast, fetchImpl: s.impl, slowMs: 40 });
  await wait(90);
  check("slow: banner visible while waiting", getServerStatus() === "waking");
  const r = await p;
  check("slow: succeeds in a single attempt", r.status === 200 && s.calls() === 1);
  check("slow: banner hidden after", getServerStatus() === "ok" && t.seen.join(",") === "waking,ok", JSON.stringify(t.seen));
  t.stop();
}

// 6. once awake: POST is not retried (no duplicate creates); GET still is
{
  resetServerState();
  await wakeFetch("http://x/health", {}, { ...fast, fetchImpl: scripted([200]).impl });
  const post = scripted(["net", 200]);
  let threw = false;
  try {
    await wakeFetch("http://x/sources", { method: "POST", body: "{}" }, { ...fast, fetchImpl: post.impl });
  } catch {
    threw = true;
  }
  check("awake: POST network error not retried", threw && post.calls() === 1, `${threw}/${post.calls()}`);
  const get = scripted(["net", 200]);
  const r = await wakeFetch("http://x/sources", {}, { ...fast, fetchImpl: get.impl });
  check("awake: GET network error retried", r.status === 200 && get.calls() === 2);
}

// 7. before the first success, even a POST is retried (the server was asleep, nothing was processed)
{
  resetServerState();
  const s = scripted(["net", "net", 202]);
  const r = await wakeFetch("http://x/sources", { method: "POST", body: "{}" }, { ...fast, fetchImpl: s.impl });
  check("first request POST retried while server wakes", r.status === 202 && s.calls() === 3);
}

// 8. gives up at the deadline with a friendly error and hides the banner
{
  resetServerState();
  const s = scripted(["net"]);
  const t = track();
  const started = Date.now();
  let err: unknown;
  try {
    await wakeFetch("http://x/sources", {}, { backoffMs: [20], sleep: wait, fetchImpl: s.impl, deadlineMs: 200 });
  } catch (e) {
    err = e;
  }
  const elapsed = Date.now() - started;
  check("deadline: throws ServerUnavailableError", err instanceof ServerUnavailableError);
  check("deadline: stops near the deadline (200ms)", elapsed >= 190 && elapsed < 600, `${elapsed}ms`);
  check("deadline: banner hidden after giving up", getServerStatus() === "ok");
  check("deadline: friendly message", String((err as Error)?.message).includes("starting up"));
  t.stop();
}

// 9. an attempt that hangs forever is cut off at the deadline
{
  resetServerState();
  const hang = ((_u: string, init: RequestInit = {}) =>
    new Promise((_res, rej) => init.signal?.addEventListener("abort", () => rej(new DOMException("a", "AbortError"))))) as unknown as typeof fetch;
  const started = Date.now();
  let err: unknown;
  try {
    await wakeFetch("http://x/sources", {}, { ...fast, fetchImpl: hang, deadlineMs: 150 });
  } catch (e) {
    err = e;
  }
  check("hung request: aborted at deadline -> ServerUnavailableError", err instanceof ServerUnavailableError && Date.now() - started < 500);
}

// 10. caller cancellation propagates immediately and stops retrying
{
  resetServerState();
  const s = scripted(["net"]);
  const ctl = new AbortController();
  const p = wakeFetch("http://x/sources", { signal: ctl.signal }, { backoffMs: [50], sleep: wait, fetchImpl: s.impl, deadlineMs: 5000 });
  await wait(20);
  ctl.abort();
  let name = "";
  try {
    await p;
  } catch (e) {
    name = (e as Error).name;
  }
  const callsAtAbort = s.calls();
  await wait(120);
  check("abort: rejects with AbortError", name === "AbortError", name);
  check("abort: no further attempts after cancel", s.calls() <= callsAtAbort + 1, `${callsAtAbort} -> ${s.calls()}`);
}

// 11. silent warm-up never shows the banner
{
  resetServerState();
  const s = scripted(["net", "net", 200]);
  const t = track();
  await wakeFetch("http://x/health", {}, { ...fast, fetchImpl: s.impl, silent: true, slowMs: 1 });
  check("silent: retries but never shows banner", s.calls() === 3 && t.seen.length === 0, JSON.stringify(t.seen));
  t.stop();
}

// 12. long idle gap re-arms wake handling (server has probably gone back to sleep)
{
  resetServerState();
  await wakeFetch("http://x/health", {}, { ...fast, fetchImpl: scripted([200]).impl });
  const realNow = Date.now;
  Date.now = () => realNow() + 15 * 60_000;
  try {
    const s = scripted(["net", 200]);
    const r = await wakeFetch("http://x/sources", { method: "POST", body: "{}" }, { ...fast, fetchImpl: s.impl });
    check("idle >14min: first request treated as waking again (POST retried)", r.status === 200 && s.calls() === 2);
  } finally {
    Date.now = realNow;
  }
}

// 13. two concurrent requests: banner stays up until BOTH are done
{
  resetServerState();
  const quick = scripted([200], 20);
  const slow = scripted(["net", "net", "net", "net", 200]);
  const t = track();
  const pa = wakeFetch("http://x/a", {}, { backoffMs: [30], sleep: wait, fetchImpl: slow.impl });
  await wait(5);
  const pb = wakeFetch("http://x/b", {}, { backoffMs: [30], sleep: wait, fetchImpl: quick.impl, slowMs: 1 });
  await pb;
  check("concurrent: banner still up while another request waits", getServerStatus() === "waking", getServerStatus());
  await pa;
  check("concurrent: banner hidden after all done", getServerStatus() === "ok");
  t.stop();
}

console.log(failed ? `\n${failed} FAILED` : "\nALL PASSED");
process.exit(failed ? 1 : 0);
