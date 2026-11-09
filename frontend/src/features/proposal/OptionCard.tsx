import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { VisualOption, Widget } from "../../api/types";
import { WidgetChart } from "../../charts/WidgetChart";
import { INCLUDED } from "./approval";
import { scoreSummary } from "./score";

const BADGE: Record<string, string> = {
  recommended: "bg-accent/10 text-accent",
  requested: "bg-emerald-50 text-emerald-700",
  needs_approval: "bg-amber-50 text-amber-800",
  optional: "bg-surface-muted text-ink-muted",
  excluded: "bg-surface-muted text-ink-muted line-through",
};

export function Thumbnail({ option, datasetId }: { option: VisualOption; datasetId: string | undefined }) {
  const q = useQuery({
    queryKey: ["preview", datasetId, option.id, option.query, option.chart_type],
    queryFn: () => api.query(datasetId as string, { ...option.query, limit: Math.min(option.query.limit ?? 50, 50) }, option.measures ?? []),
    enabled: !!datasetId,
    staleTime: 60_000,
  });
  const widget = { id: option.id, type: option.chart_type, title: option.title, query: option.query,
                   encoding: option.encoding, options: option.options, thresholds: option.thresholds } as Widget;
  if (q.isLoading) return <div className="h-full animate-pulse rounded bg-surface-muted" aria-label="Loading preview" />;
  if (q.isError || !q.data) return <p className="text-xs text-ink-muted">No preview</p>;
  return <WidgetChart widget={widget} result={q.data} measures={option.measures ?? []} compact />;
}

export function OptionCard({ option, datasetId, onToggle, disabled }: {
  option: VisualOption;
  datasetId: string | undefined;
  onToggle: (included: boolean) => void;
  disabled?: boolean;
}) {
  const included = INCLUDED.has(option.status);
  const fields = (option.fields ?? []).join(", ");
  return (
    <article aria-label={option.title} className={`flex flex-col rounded-lg border bg-surface p-3 ${included ? "border-border" : "border-dashed border-border opacity-70"}`}>
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-medium" title={option.title}>{option.title}</h3>
          <p className="text-xs text-ink-muted">{option.chart_type.replace("_", " ")}{fields && ` · ${fields}`}</p>
        </div>
        <span tabIndex={0} title={scoreSummary(option)} aria-label={`Status ${option.status.replace("_", " ")}. Score: ${scoreSummary(option)}`}
              className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] ${BADGE[option.status] ?? ""}`}>
          {option.status.replace("_", " ")}
        </span>
      </header>
      <div className="mt-2 h-28">{included && <Thumbnail option={option} datasetId={datasetId} />}</div>
      {option.rationale && <p className="mt-2 line-clamp-2 text-xs text-ink-muted" title={option.rationale}>{option.rationale}</p>}
      <label className="mt-2 flex items-center gap-2 text-xs">
        <input type="checkbox" checked={included} disabled={disabled || option.status === "needs_approval"}
               onChange={(e) => onToggle(e.target.checked)} aria-label={`Include ${option.title}`} />
        Include
      </label>
    </article>
  );
}
