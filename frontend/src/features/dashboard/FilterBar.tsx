import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { DashboardSpec } from "../../api/types";

type Filters = Record<string, string[]>;

function FilterControl({ field, label, datasetId, value, onChange }: {
  field: string; label: string; datasetId: string; value: string[]; onChange: (v: string[]) => void;
}) {
  const q = useQuery({
    queryKey: ["filter-values", datasetId, field],
    queryFn: () => api.query(datasetId, { dataset: "ds", dimensions: [field], measures: ["n"], limit: 100,
                                          sort: [{ field: "n", dir: "desc" }] }, [{ id: "n", expr: "count()" }]),
    staleTime: 60_000,
  });
  const values = (q.data?.rows ?? []).map((r) => String(r[0]));
  return (
    <label className="flex items-center gap-1 text-xs">
      <span className="text-ink-muted">{label}</span>
      <select multiple={false} value={value[0] ?? ""} onChange={(e) => onChange(e.target.value ? [e.target.value] : [])}
              className="rounded border border-border bg-surface px-1 py-0.5" aria-label={`Filter by ${label}`}>
        <option value="">All</option>
        {values.map((v) => <option key={v} value={v}>{v}</option>)}
      </select>
    </label>
  );
}

/** Global filters declared in the spec; values come from the dataset. */
export function FilterBar({ spec, filters, onChange }: { spec: DashboardSpec; filters: Filters; onChange: (f: Filters) => void }) {
  const datasetId = spec.datasets[0]?.dataset_id;
  if (!spec.filters?.length || !datasetId) return null;
  return (
    <div className="flex flex-wrap items-center gap-3" role="group" aria-label="Dashboard filters">
      {spec.filters.map((f) => (
        <FilterControl key={f.id} field={f.field} label={f.label ?? f.field} datasetId={datasetId}
                       value={filters[f.field] ?? []} onChange={(v) => onChange({ ...filters, [f.field]: v })} />
      ))}
    </div>
  );
}
