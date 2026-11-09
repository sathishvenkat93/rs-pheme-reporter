// Number and label formatting for axes, tooltips and KPI tiles.
export type MeasureFormat = "number" | "percent" | "duration_ms" | "bytes" | "rate" | null | undefined;

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const plain = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

export function formatValue(value: unknown, format?: MeasureFormat): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  switch (format) {
    case "percent":
      return `${plain.format(n * 100)}%`;
    case "duration_ms":
      if (Math.abs(n) >= 60_000) return `${plain.format(n / 60_000)} min`;
      if (Math.abs(n) >= 1000) return `${plain.format(n / 1000)} s`;
      return `${plain.format(n)} ms`;
    case "bytes": {
      const units = ["B", "KB", "MB", "GB", "TB"];
      let i = 0;
      let v = Math.abs(n);
      while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i += 1;
      }
      return `${n < 0 ? "-" : ""}${plain.format(v)} ${units[i]}`;
    }
    case "rate":
      return `${plain.format(n)}/s`;
    default:
      return Math.abs(n) >= 10_000 ? compact.format(n) : plain.format(n);
  }
}

/** Shorten long category labels; the full text stays in the tooltip. */
export function shortLabel(value: unknown, max = 18): string {
  const s = value === null || value === undefined ? "(blank)" : String(value);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** ISO timestamps on an axis: keep it short (date, or time when within one day). */
export function axisTime(value: unknown): string {
  const s = String(value);
  if (/^\d{4}-\d{2}-\d{2}T/.test(s)) {
    const d = new Date(s);
    if (!Number.isNaN(d.getTime())) {
      return s.slice(11, 16) === "00:00" ? s.slice(0, 10) : `${s.slice(5, 10)} ${s.slice(11, 16)}`;
    }
  }
  return shortLabel(s);
}
