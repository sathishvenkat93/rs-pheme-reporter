// Live chart instances by widget id, so the dashboard can export them to PNG.
import type { ECharts } from "echarts";

export const chartRegistry = new Map<string, ECharts>();
