import * as echarts from "echarts";
import { useEffect, useRef } from "react";
import type { EChartsOption } from "echarts";
import { chartRegistry } from "./registry";

interface Props {
  option: EChartsOption;
  label: string;
  id?: string; // widget id: registers the instance for PNG export
  className?: string;
}

/** Thin ECharts wrapper (SVG renderer), resized with its container. */
export function Chart({ option, label, id, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const instance = echarts.init(ref.current, undefined, { renderer: "svg" });
    chart.current = instance;
    if (id) chartRegistry.set(id, instance);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => instance.resize());
    observer?.observe(ref.current);
    return () => {
      observer?.disconnect();
      if (id) chartRegistry.delete(id);
      instance.dispose();
      chart.current = null;
    };
  }, [id]);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);

  return <div ref={ref} role="img" aria-label={label} className={className ?? "h-full w-full"} />;
}
