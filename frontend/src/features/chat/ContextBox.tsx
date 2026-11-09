import { useState } from "react";
import { api } from "../../api/client";
import { useSession } from "../session/SessionContext";

/** Free-text context such as SLAs and thresholds ("p95 must be < 800 ms, env=staging"). */
export function ContextBox() {
  const { sessionId } = useSession();
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-ink-muted">Add context (targets, SLAs)</summary>
      <form
        className="mt-2 flex gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!text.trim()) return;
          await api.context(sessionId, text.trim());
          setSaved(text.trim());
          setText("");
        }}
      >
        <label className="sr-only" htmlFor="context-input">Context</label>
        <input id="context-input" value={text} onChange={(e) => setText(e.target.value)}
               placeholder="e.g. pass rate should be at least 95 %"
               className="min-w-0 flex-1 rounded border border-border px-2 py-1" />
        <button type="submit" className="rounded border border-border px-2 py-1 hover:border-accent">Save</button>
      </form>
      {saved && <p className="mt-1 text-xs text-ink-muted">Saved: {saved}</p>}
    </details>
  );
}
