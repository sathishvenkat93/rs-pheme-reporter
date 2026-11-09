import { useState } from "react";
import type { Measure, QueryResult, Widget } from "../../api/types";
import { WidgetChart } from "../../charts/WidgetChart";

interface Props {
  widget: Widget;
  result: QueryResult | string | undefined;
  loading: boolean;
  measures: Measure[];
  selected: boolean;
  readOnly: boolean;
  onSelect: (additive: boolean) => void;
  onRename: (title: string) => void;
}

export function WidgetCard({ widget, result, loading, measures, selected, readOnly, onSelect, onRename }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(widget.title);

  function commit() {
    setEditing(false);
    if (draft.trim() && draft.trim() !== widget.title) onRename(draft);
    else setDraft(widget.title);
  }

  let body;
  if (loading) body = <div className="h-full animate-pulse rounded bg-surface-muted" aria-label="Loading" />;
  else if (typeof result === "string") body = <p role="alert" className="p-2 text-xs text-red-700">{result}</p>;
  else if (!result || result.rows.length === 0) body = <p className="p-2 text-xs text-ink-muted">No data for the current filters.</p>;
  else body = <WidgetChart widget={widget} result={result} measures={measures} register />;

  return (
    <article
      aria-label={widget.title}
      aria-selected={selected}
      data-testid={`widget-${widget.id}`}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("input,button")) return;
        onSelect(e.shiftKey);
      }}
      className={`flex h-full flex-col rounded-lg border bg-surface p-2 ${selected ? "border-accent ring-2 ring-accent/30" : "border-border"}`}
    >
      <header className="flex items-center gap-2">
        {!readOnly && <span className="drag-handle cursor-move select-none text-ink-muted" aria-hidden="true">⋮⋮</span>}
        {editing ? (
          <input
            autoFocus
            aria-label={`Rename ${widget.title}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") { setDraft(widget.title); setEditing(false); }
            }}
            className="min-w-0 flex-1 rounded border border-accent px-1 text-sm"
          />
        ) : (
          <h3 className="min-w-0 flex-1 truncate text-sm font-medium" title={widget.description ?? widget.title}
              onDoubleClick={() => !readOnly && setEditing(true)}>
            {widget.title}
          </h3>
        )}
        {!readOnly && !editing && (
          <button type="button" className="text-xs text-ink-muted hover:text-accent" aria-label={`Rename ${widget.title}`} onClick={() => setEditing(true)}>
            ✎
          </button>
        )}
        <button type="button" className="text-xs text-ink-muted hover:text-accent" aria-pressed={selected}
                aria-label={`${selected ? "Deselect" : "Select"} ${widget.title}`} onClick={(e) => onSelect(e.shiftKey)}>
          {selected ? "●" : "○"}
        </button>
      </header>
      <div className="mt-1 min-h-0 flex-1">{body}</div>
    </article>
  );
}
