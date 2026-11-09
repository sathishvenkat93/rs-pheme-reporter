import { useParams } from "react-router";
import { ChatPane } from "../features/chat/ChatPane";
import { DashboardView } from "../features/dashboard/DashboardView";
import { ProposalPane } from "../features/proposal/ProposalPane";
import { SessionProvider, useSession } from "../features/session/SessionContext";

function Panes({ version }: { version?: number }) {
  const { session } = useSession();
  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(300px,380px)_1fr] xl:grid-cols-[minmax(320px,420px)_1fr]">
      <ChatPane />
      <section aria-label="Canvas" className="min-h-0 overflow-hidden">
        {session?.dashboard_id ? <DashboardView dashboardId={session.dashboard_id} version={version} /> : <ProposalPane />}
      </section>
    </div>
  );
}

/** Two-pane workspace for a session: chat on the left, proposal or dashboard on the right. */
export function Workspace() {
  const { sid } = useParams();
  if (!sid) return null;
  return (
    <SessionProvider sessionId={sid} key={sid}>
      <Panes />
    </SessionProvider>
  );
}

export function SessionPanes({ sessionId, version }: { sessionId: string; version?: number }) {
  return (
    <SessionProvider sessionId={sessionId} key={sessionId}>
      <Panes version={version} />
    </SessionProvider>
  );
}
