// Pinned favourites, per browser (localStorage; falls back to memory when storage is blocked).
const KEY = "pheme.pins";
let memory: string[] = [];

export function loadPins(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as string[]) : memory;
  } catch {
    return memory;
  }
}

export function togglePin(id: string): string[] {
  const pins = loadPins();
  const next = pins.includes(id) ? pins.filter((p) => p !== id) : [...pins, id];
  memory = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage blocked: keep in memory for this page
  }
  return next;
}

/** Pinned first, then most recently updated. */
export function sortDashboards<T extends { id: string; updated_at: string }>(items: T[], pins: string[]): T[] {
  return [...items].sort((a, b) => Number(pins.includes(b.id)) - Number(pins.includes(a.id)) || b.updated_at.localeCompare(a.updated_at));
}
