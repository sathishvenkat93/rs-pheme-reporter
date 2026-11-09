import { useSession } from "../session/SessionContext";
import { ChatPanel } from "./ChatPanel";
import { ContextBox } from "./ContextBox";
import { EditPreview } from "./EditPreview";
import { UploadZone } from "./UploadZone";

/** Left pane: upload, context and the conversation. */
export function ChatPane() {
  const { session, error } = useSession();
  const planning = !session?.dashboard_id;
  return (
    <section aria-label="Chat" className="flex h-full min-h-0 flex-col gap-3 border-r border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-ink-muted">Chat</h2>
        {session && <span className="rounded bg-surface-muted px-2 py-0.5 text-xs text-ink-muted" data-testid="session-state">{session.state.toLowerCase()}</span>}
      </div>
      {planning && <UploadZone />}
      {planning && <ContextBox />}
      {error && <p role="alert" className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{error}</p>}
      <EditPreview />
      <ChatPanel />
    </section>
  );
}
