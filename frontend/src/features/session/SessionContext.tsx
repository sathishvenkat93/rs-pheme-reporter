import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useState, type ReactNode } from "react";
import { api, API, ApiError } from "../../api/client";
import type { DecisionChoice, DraftPlan, Session } from "../../api/types";
import { initialLive, reduce, staleKeys, type LiveAction, type LiveState, type TimelineItem } from "./timeline";

interface SessionValue {
  sessionId: string;
  session: Session | undefined;
  plan: DraftPlan | undefined;
  live: LiveState;
  dispatch: (a: LiveAction) => void;
  selected: string[];
  setSelected: (ids: string[]) => void;
  send: (text: string) => Promise<void>;
  approve: () => Promise<void>;
  decide: (decisionId: string, choice: DecisionChoice, detail?: string) => Promise<void>;
  toggle: (optionId: string, included: boolean) => Promise<void>;
  applyEdit: () => Promise<void>;
  discardEdit: () => Promise<void>;
  refresh: () => void;
  error: string | null;
  setError: (e: string | null) => void;
}

const Ctx = createContext<SessionValue | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useSession(): SessionValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

let counter = 0;
const nextId = () => `local-${++counter}`;

export function SessionProvider({ sessionId, children }: { sessionId: string; children: ReactNode }) {
  const qc = useQueryClient();
  const [live, dispatch] = useReducer(reduce, initialLive);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const sessionQ = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId) });
  const planId = sessionQ.data?.plan_id ?? null;
  const planQ = useQuery({ queryKey: ["plan", planId], queryFn: () => api.plan(planId as string), enabled: !!planId });

  useEffect(() => {
    api.messages(sessionId).then(
      (msgs) => dispatch({ type: "history", items: msgs.map((m, i): TimelineItem => ({ kind: m.role === "user" ? "user" : "assistant", id: `h${i}`, text: m.text })) }),
      () => undefined,
    );
  }, [sessionId]);

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const source = new EventSource(`${API}/sessions/${sessionId}/events?after=$`);
    const kinds = ["assistant_delta", "assistant_message", "tool_progress", "options", "plan_diff", "approval_card",
                   "approval_confirm", "state", "decision", "edit_preview", "dashboard", "error"];
    const handlers = kinds.map((kind) => {
      const fn = (e: MessageEvent) => {
        const data = JSON.parse(e.data) as Record<string, unknown>;
        dispatch({ type: "event", event: kind, data });
        for (const key of staleKeys(kind)) void qc.invalidateQueries({ queryKey: [key] });
      };
      source.addEventListener(kind, fn as EventListener);
      return [kind, fn] as const;
    });
    return () => {
      for (const [kind, fn] of handlers) source.removeEventListener(kind, fn as EventListener);
      source.close();
    };
  }, [sessionId, qc]);

  const invalidate = useCallback(() => {
    for (const key of ["session", "plan", "dashboard", "versions", "data", "library"]) void qc.invalidateQueries({ queryKey: [key] });
  }, [qc]);

  const guard = useCallback(async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      invalidate();
    }
  }, [invalidate]);

  const value = useMemo<SessionValue>(() => ({
    sessionId,
    session: sessionQ.data,
    plan: planQ.data,
    live,
    dispatch,
    selected,
    setSelected,
    error,
    setError,
    refresh: invalidate,
    send: async (text: string) => {
      dispatch({ type: "sent", id: nextId(), text });
      try {
        const res = await api.send(sessionId, text, selected);
        dispatch({ type: "replied", id: nextId(), text: res.reply });
      } catch (e) {
        dispatch({ type: "failed", id: nextId(), text: e instanceof ApiError ? e.message : "The message couldn't be sent." });
      } finally {
        invalidate();
      }
    },
    approve: () => guard(async () => { await api.approve(sessionId); dispatch({ type: "clearConfirm" }); }),
    decide: (id, choice, detail) => guard(() => api.decide(id, choice, detail)),
    toggle: (optionId, included) => guard(() => api.toggleOption(sessionId, optionId, included)),
    applyEdit: () => guard(async () => { await api.applyEdit(sessionId); dispatch({ type: "clearPreview" }); }),
    discardEdit: () => guard(async () => { await api.discardEdit(sessionId); dispatch({ type: "clearPreview" }); }),
  }), [sessionId, sessionQ.data, planQ.data, live, selected, error, invalidate, guard]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
