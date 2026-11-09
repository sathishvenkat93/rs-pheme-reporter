import { useState } from "react";
import { api } from "../../api/client";
import { UploadZone } from "../chat/UploadZone";

/** "Upload new data" for live dashboards: append, then refresh; show a mapping when columns differ. */
export function RefreshPanel({ dashboardId, datasetId, onDone }: { dashboardId: string; datasetId: string; onDone: () => void }) {
  const [mapping, setMapping] = useState<Record<string, string | null> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  return (
    <div className="rounded-lg border border-border bg-surface p-3" aria-label="Upload new data">
      <UploadZone datasetId={datasetId} onDone={async (uploadId) => {
        const res = await api.refresh(dashboardId, uploadId);
        if (res.status === "refreshed") { setMessage("The dashboard now includes the new data."); onDone(); }
        else { setMapping(res.mapping ?? {}); setMessage("This file's columns don't match. Proposed mapping:"); }
      }} />
      {message && <p className="mt-2 text-xs" role="status">{message}</p>}
      {mapping && (
        <table className="mt-1 text-xs">
          <caption className="sr-only">Proposed field mapping</caption>
          <thead><tr><th className="pr-4 text-left">Dashboard field</th><th className="text-left">File column</th></tr></thead>
          <tbody>{Object.entries(mapping).map(([k, v]) => <tr key={k}><td className="pr-4">{k}</td><td>{v ?? "— no match"}</td></tr>)}</tbody>
        </table>
      )}
    </div>
  );
}
