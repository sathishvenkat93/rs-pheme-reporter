// Theme tokens shared by every chart. Status tokens colour thresholds (good / warning / critical).
export const theme = {
  ink: "#1d232b",
  muted: "#5b6573",
  grid: "#e3e6eb",
  accent: "#3b5bdb",
  series: ["#3b5bdb", "#0ca678", "#f59f00", "#ae3ec9", "#1098ad", "#e8590c", "#5c940d", "#868e96"],
  status: { good: "#2f9e44", warning: "#f08c00", critical: "#e03131" },
  font: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
} as const;

export type Status = keyof typeof theme.status;
