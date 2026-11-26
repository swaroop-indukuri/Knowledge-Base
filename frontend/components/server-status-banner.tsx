"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { getServerStatus, getWakingSince, subscribeServerStatus } from "@/lib/wake";
import { Spinner } from "./icons";

/** Friendly notice while a sleeping free-tier backend boots (see lib/wake.ts). */
export default function ServerStatusBanner() {
  const status = useSyncExternalStore(subscribeServerStatus, getServerStatus, () => "ok" as const);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (status !== "waking") return;
    const tick = () => setElapsed(Math.floor((Date.now() - (getWakingSince() ?? Date.now())) / 1000));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [status]);

  if (status !== "waking") return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-center gap-3 bg-amber-100 px-4 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200"
    >
      <Spinner className="h-4 w-4 shrink-0" />
      <span>
        <strong>Waking up the server…</strong> The free-tier backend sleeps when idle, so the first
        request can take up to a minute. Hang tight ({elapsed}s)
      </span>
    </div>
  );
}
