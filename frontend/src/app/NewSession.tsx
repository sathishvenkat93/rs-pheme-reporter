import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";

/** "/" starts a fresh session and moves to its URL. */
export function NewSession() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api.createSession().then((s) => navigate(`/s/${s.id}`, { replace: true }), () => setError("Pheme's server isn't reachable."));
  }, [navigate]);
  return <p className="p-6 text-sm text-ink-muted" role={error ? "alert" : undefined}>{error ?? "Starting a session…"}</p>;
}
