import { useRef, useState } from "react";
import { uploadFile, waitForUpload, ApiError } from "../../api/client";
import { useSession } from "../session/SessionContext";

interface Item {
  name: string;
  progress: number;
  status: "uploading" | "processing" | "done" | "failed";
  error?: string;
}

/** Drop files (or pick them). Files after the first are appended to the same dataset. */
export function UploadZone({ datasetId: fixedDataset, onDone }: { datasetId?: string; onDone?: (uploadId: string) => void }) {
  const { sessionId, session, refresh } = useSession();
  const [items, setItems] = useState<Item[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const set = (i: number, patch: Partial<Item>) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  async function handle(files: FileList | null) {
    if (!files?.length) return;
    let datasetId = fixedDataset ?? session?.dataset_ids?.[0];
    const start = items.length;
    setItems((xs) => [...xs, ...Array.from(files, (f) => ({ name: f.name, progress: 0, status: "uploading" as const }))]);
    for (const [k, file] of Array.from(files).entries()) {
      const i = start + k;
      try {
        const fields: Record<string, string> = fixedDataset ? {} : { session_id: sessionId };
        if (datasetId) fields.dataset_id = datasetId;
        const created = await uploadFile(file, fields, (p) => set(i, { progress: p }));
        set(i, { status: "processing", progress: 1 });
        const done = await waitForUpload(created.id);
        if (done.status === "failed") set(i, { status: "failed", error: done.error ?? "The file couldn't be read." });
        else {
          set(i, { status: "done" });
          datasetId = datasetId ?? done.dataset_ids[0];
          onDone?.(done.id);
        }
      } catch (e) {
        set(i, { status: "failed", error: e instanceof ApiError ? e.message : "Upload failed." });
      }
    }
    refresh();
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload files: drop logs here or press Enter to choose"
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); void handle(e.dataTransfer.files); }}
        className={`cursor-pointer rounded-lg border border-dashed p-4 text-center text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${over ? "border-accent bg-accent/5" : "border-border text-ink-muted hover:border-accent"}`}
      >
        Drop JUnit, JMeter, CSV, JSON or Excel files here, or <span className="text-accent underline">choose files</span>
        <input ref={input} type="file" multiple className="sr-only" data-testid="file-input" onChange={(e) => void handle(e.target.files)} aria-hidden="true" tabIndex={-1} />
      </div>
      {items.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs" aria-label="Uploads">
          {items.map((it, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className="truncate" title={it.name}>{it.name}</span>
              {it.status === "uploading" && (
                <progress className="h-1.5 w-24" max={1} value={it.progress} aria-label={`Uploading ${it.name}`} />
              )}
              <span className={it.status === "failed" ? "text-red-600" : "text-ink-muted"}>
                {it.status === "processing" ? "processing…" : it.status === "failed" ? it.error : it.status === "done" ? "ready" : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
