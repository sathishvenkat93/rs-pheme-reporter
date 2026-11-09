/** Left pane. TODO(M10): upload drop zone, context box and SSE chat. */
export function ChatPane() {
  return (
    <section aria-label="Chat" className="flex h-full flex-col border-r border-border bg-surface p-4">
      <h2 className="text-sm font-medium text-ink-muted">Chat</h2>
      <p className="mt-4 text-sm text-ink-muted">Upload logs to start a session.</p>
    </section>
  );
}
