const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

export function isVideoId(value: unknown): value is string {
  return typeof value === "string" && VIDEO_ID.test(value);
}

/** Extract the video id from any common YouTube URL shape, or null. */
export function youtubeIdFromUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "").replace(/^m\./, "");
    let id: string | null = null;
    if (host === "youtu.be") id = u.pathname.split("/")[1] ?? null;
    else if (host === "youtube.com" || host === "music.youtube.com") {
      if (u.pathname === "/watch") id = u.searchParams.get("v");
      else {
        const [kind, rest] = u.pathname.split("/").filter(Boolean);
        if (["shorts", "embed", "live", "v"].includes(kind)) id = rest ?? null;
      }
    }
    return isVideoId(id) ? id : null;
  } catch {
    return null;
  }
}

/** Link that opens the video at a given time: youtube.com/watch?v=ID&t=142s */
export function youtubeTimestampUrl(videoId: string, seconds?: number | null): string {
  const base = `https://www.youtube.com/watch?v=${videoId}`;
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return base;
  return `${base}&t=${Math.floor(seconds)}s`;
}

/** 142 -> "2:22", 3723 -> "1:02:03" */
export function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
