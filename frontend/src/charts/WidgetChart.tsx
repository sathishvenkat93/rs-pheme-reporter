import { useMemo } from "react";
import type { Measure, QueryResult, Widget } from "../api/types";
import { Chart } from "./Chart";
import { chartOption, compactOption } from "./options";
import { TableView } from "./TableView";

/** Renders any widget type from its spec and rows. */
export function WidgetChart({ widget, result, measures, register, compact }: {
  widget: Widget;
  result: Pick<QueryResult, "columns" | "rows">;
  measures: Measure[];
  register?: boolean;
  compact?: boolean; // thumbnails
}) {
  const input = useMemo(() => ({ widget, result, measures }), [widget, result, measures]);
  const option = useMemo(() => (compact && widget.type !== "kpi" ? compactOption(chartOption(input)) : chartOption(input)), [input, compact, widget.type]);
  if (widget.type === "table") return <TableView input={input} />;
  return <Chart option={option} label={`${widget.title} (${widget.type.replace("_", " ")} chart)`} id={register ? widget.id : undefined} />;
}
