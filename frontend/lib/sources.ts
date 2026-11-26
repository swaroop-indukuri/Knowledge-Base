import type { Source } from "@/lib/api";

/** A source stuck in "processing" this long is treated as stalled (e.g. server restarted). */
export const STALE_MS = 10 * 60 * 1000;

export const isStale = (s: Source) =>
  s.status === "processing" && Date.now() - new Date(s.created_at).getTime() > STALE_MS;

export const typeLabel: Record<Source["type"], string> = {
  url: "Web article",
  pdf: "PDF",
  youtube: "YouTube video",
};

export const categoryKey = (category: string | null | undefined) => category?.trim().toLowerCase() ?? "";

export interface CategoryOption {
  key: string;
  label: string;
  count: number;
}

/** Distinct categories across the user's sources (case-insensitive), alphabetical. */
export function deriveCategories(sources: Source[]): CategoryOption[] {
  const map = new Map<string, CategoryOption>();
  for (const s of sources) {
    const label = s.category?.trim();
    if (!label) continue;
    const key = label.toLowerCase();
    const existing = map.get(key);
    if (existing) existing.count += 1;
    else map.set(key, { key, label, count: 1 });
  }
  return Array.from(map.values()).sort((a, b) => a.label.localeCompare(b.label));
}

export function matchesQuery(s: Source, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [s.title, s.summary, s.category, s.url].some((v) => v?.toLowerCase().includes(q));
}
