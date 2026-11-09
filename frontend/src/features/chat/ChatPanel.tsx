import { useEffect, useRef, useState } from "react";
import { useSession } from "../session/SessionContext";

export function ChatPanel() {
  const { live, send, plan, session, selected, setSelected, decide } = useSession();
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Block body on purpose: newer browsers return a Promise from scrollIntoView, and an
    // effect must return nothing or a cleanup function.
    void end.current?.scrollIntoView?.({ block: "end" });
  }, [live.items.length, live.streaming]);

  const clarify = plan?.decisions?.find((d) => !d.choice && d.options.includes("choose_candidate") && d.candidates?.length);
  const canChat = session?.state !== "INGEST" && session?.state !== "PROFILE";

  async function submit(message: string) {
    if (!message.trim() || live.pending) return;
    setText("");
    await send(message.trim());
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ol className="min-h-0 flex-1 space-y-2 overflow-y-auto py-2" aria-label="Conversation" aria-live="polite">
        {live.items.map((it) =>
          it.kind === "diff" ? (
            <li key={it.id} className="rounded border border-border bg-surface-muted px-2 py-1 text-xs text-ink-muted">
              <span className="font-medium text-ink">Plan updated:</span> {it.changes.join(" · ")}
            </li>
          ) : (
            <li key={it.id} className={`max-w-[90%] rounded-lg px-3 py-2 text-sm ${it.kind === "user" ? "ml-auto bg-accent text-white" : it.kind === "error" ? "bg-red-50 text-red-700" : "bg-surface-muted"}`}>
              {it.text}
            </li>
          ),
        )}
        {live.pending && (
          <li className="max-w-[90%] rounded-lg bg-surface-muted px-3 py-2 text-sm text-ink-muted" aria-label="Pheme is replying">
            {live.streaming || live.progress || "Thinking…"}
          </li>
        )}
        <div ref={end} />
      </ol>
      {clarify && (
        <div className="flex flex-wrap gap-1 pb-2" role="group" aria-label="Quick replies">
          <span className="text-xs text-ink-muted">{clarify.reason}</span>
          {clarify.candidates?.map((c) => (
            <button key={c} type="button" onClick={() => void decide(clarify.id, "choose_candidate", c)}
                    className="rounded-full border border-accent px-2 py-0.5 text-xs text-accent hover:bg-accent/10">
              {c.replaceAll("_", " ")}
            </button>
          ))}
        </div>
      )}
      {selected.length > 0 && (
        <div className="mb-1 flex items-center gap-2 text-xs">
          <span className="rounded bg-accent/10 px-2 py-0.5 text-accent" data-testid="selection-chip">
            Editing: {selected.length === 1 ? selected[0].replace(/^w_/, "").replaceAll("_", " ") : `${selected.length} widgets`}
          </span>
          <button type="button" className="text-ink-muted underline" onClick={() => setSelected([])}>clear</button>
        </div>
      )}
      <form className="flex gap-2 border-t border-border pt-2" onSubmit={(e) => { e.preventDefault(); void submit(text); }}>
        <label htmlFor="chat-input" className="sr-only">Message Pheme</label>
        <input id="chat-input" value={text} onChange={(e) => setText(e.target.value)} disabled={!canChat}
               placeholder={canChat ? "Ask for a chart, or say what to change…" : "Upload a file to start"}
               className="min-w-0 flex-1 rounded border border-border px-2 py-1.5 text-sm disabled:bg-surface-muted" />
        <button type="submit" disabled={!canChat || live.pending || !text.trim()}
                className="rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-40">Send</button>
      </form>
    </div>
  );
}
