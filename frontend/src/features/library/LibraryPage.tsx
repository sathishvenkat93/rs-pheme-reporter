import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { loadPins, sortDashboards, togglePin } from "./pins";

export function LibraryPage() {
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("");
  const [tag, setTag] = useState("");
  const [pins, setPins] = useState<string[]>(loadPins);
  const params = Object.fromEntries(Object.entries({ q, domain, tag }).filter(([, v]) => v));
  const list = useQuery({ queryKey: ["library", params], queryFn: () => api.dashboards(params) });
  const all = useQuery({ queryKey: ["library", {}], queryFn: () => api.dashboards() });
  const domains = [...new Set((all.data ?? []).map((d) => d.domain).filter(Boolean))] as string[];
  const tags = [...new Set((all.data ?? []).flatMap((d) => d.tags))];
  const items = sortDashboards(list.data ?? [], pins);

  return (
    <section aria-label="Library" className="mx-auto max-w-4xl p-6">
      <h1 className="text-lg font-semibold">Library</h1>
      <div className="mt-4 flex flex-wrap gap-3 text-sm" role="search">
        <label className="flex items-center gap-1">
          <span className="sr-only">Search dashboards</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name…" aria-label="Search dashboards"
                 className="rounded border border-border px-2 py-1" />
        </label>
        <select value={domain} onChange={(e) => setDomain(e.target.value)} aria-label="Filter by domain" className="rounded border border-border px-2 py-1">
          <option value="">All domains</option>
          {domains.map((d) => <option key={d} value={d}>{d.replace("_", " ")}</option>)}
        </select>
        <select value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Filter by tag" className="rounded border border-border px-2 py-1">
          <option value="">All tags</option>
          {tags.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      {list.isLoading && <p className="mt-4 text-sm text-ink-muted">Loading…</p>}
      {!list.isLoading && items.length === 0 && <p className="mt-4 text-sm text-ink-muted">No dashboards yet.</p>}
      <ul className="mt-4 divide-y divide-border rounded-lg border border-border bg-surface" aria-label="Dashboards">
        {items.map((d) => (
          <li key={d.id} className="flex items-center gap-3 px-4 py-3">
            <button type="button" aria-pressed={pins.includes(d.id)} aria-label={`${pins.includes(d.id) ? "Unpin" : "Pin"} ${d.title}`}
                    onClick={() => setPins(togglePin(d.id))} className={pins.includes(d.id) ? "text-accent" : "text-ink-muted"}>
              {pins.includes(d.id) ? "★" : "☆"}
            </button>
            <div className="min-w-0 flex-1">
              <Link to={`/d/${d.id}`} className="font-medium hover:text-accent">{d.title}</Link>
              <p className="text-xs text-ink-muted">
                {d.domain?.replace("_", " ") ?? "general"} · v{d.version} · updated {new Date(d.updated_at).toLocaleString()}
              </p>
            </div>
            <Link to={`/d/${d.id}`} className="text-sm text-accent" aria-label={`Open ${d.title}`}>Open</Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
