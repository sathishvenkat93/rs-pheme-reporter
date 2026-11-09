import type { DashboardSpec } from "../../api/types";
import { chartRegistry } from "../../charts/registry";

const WIDTH = 1200;
const ROW = 60;
const PAD = 8;

function load(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

/** Compose every widget's chart (ECharts getDataURL) onto one canvas, laid out like the grid. */
export async function exportPng(spec: DashboardSpec): Promise<string> {
  const items = spec.layout?.items ?? [];
  const col = WIDTH / 12;
  const height = Math.max(...items.map((i) => (i.y + i.h) * ROW), ROW) + 40;
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH * 2;
  canvas.height = height * 2;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas isn't available.");
  ctx.scale(2, 2);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, WIDTH, height);
  ctx.fillStyle = "#1d232b";
  ctx.font = "600 16px system-ui, sans-serif";
  ctx.fillText(spec.title, PAD, 24);
  for (const item of items) {
    const widget = spec.widgets?.find((w) => w.id === item.widget);
    const x = item.x * col + PAD;
    const y = item.y * ROW + 40;
    const w = item.w * col - 2 * PAD;
    const h = item.h * ROW - 2 * PAD;
    ctx.font = "500 12px system-ui, sans-serif";
    ctx.fillText(widget?.title ?? item.widget, x, y + 12);
    const chart = chartRegistry.get(item.widget);
    if (!chart) continue;
    const img = await load(chart.getDataURL({ pixelRatio: 2, backgroundColor: "#ffffff" }));
    ctx.drawImage(img, x, y + 18, w, h - 18);
  }
  return canvas.toDataURL("image/png");
}

export function download(dataUrl: string, filename: string): void {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  a.click();
}
