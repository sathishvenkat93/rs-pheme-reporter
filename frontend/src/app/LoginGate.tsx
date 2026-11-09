import { useEffect, useState } from "react";
import { api, ApiError, UNAUTHORIZED } from "../api/client";

/** Shown when the API answers 401 (PHEME_AUTH_MODE=dev): email sign-in, then reload. */
export function LoginGate() {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(UNAUTHORIZED, show);
    return () => window.removeEventListener(UNAUTHORIZED, show);
  }, []);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30">
      <form role="dialog" aria-label="Sign in" className="w-80 rounded-lg bg-surface p-5 shadow-xl"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await api.login(email);
                window.location.reload();
              } catch (err) {
                setError(err instanceof ApiError && err.status === 404 ? "This server uses single sign-on. Sign in through your identity provider." : "Sign-in failed.");
              }
            }}>
        <h2 className="text-base font-semibold">Sign in to Pheme</h2>
        <label className="mt-3 block text-sm" htmlFor="login-email">Work email</label>
        <input id="login-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
               className="mt-1 w-full rounded border border-border px-2 py-1" />
        {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
        <button type="submit" className="mt-4 w-full rounded bg-accent py-1.5 text-sm text-white">Continue</button>
      </form>
    </div>
  );
}
