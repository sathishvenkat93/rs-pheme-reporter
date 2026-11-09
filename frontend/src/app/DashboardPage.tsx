import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { api } from "../api/client";
import { SessionPanes } from "./Workspace";

const KEY = (id: string) => `pheme.session.${id}`;

/** /d/:id and /d/:id/v/:n: the dashboard with a chat session bound to it (reused per tab). */
export function DashboardPage() {
  const { id, n } = useParams();
  const [sessionId, setSessionId] = useState<string | null>(() => {
    try {
      return id ? sessionStorage.getItem(KEY(id)) : null;
    } catch {
      return null;
    }
  });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!id || sessionId) return;
    api.createSession(id).then(
      (s) => {
        try {
          sessionStorage.setItem(KEY(id), s.id);
        } catch {
          // private mode: fine, a new session next time
        }
        setSessionId(s.id);
      },
      () => setError("This dashboard doesn't exist or isn't yours."),
    );
  }, [id, sessionId]);
  if (error) return <p role="alert" className="p-6 text-sm text-red-700">{error}</p>;
  if (!sessionId) return <p className="p-6 text-sm text-ink-muted">Opening the dashboard…</p>;
  return <SessionPanes sessionId={sessionId} version={n ? Number(n) : undefined} />;
}
