import { NavLink, Outlet } from "react-router";
import { LoginGate } from "./LoginGate";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded px-2 py-1 text-sm ${isActive ? "bg-surface-muted text-ink" : "text-ink-muted hover:text-ink"}`;

export function AppLayout() {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-6 border-b border-border bg-surface px-4 py-2">
        <span className="font-semibold tracking-tight">Pheme</span>
        <nav aria-label="Main" className="flex gap-2">
          <NavLink to="/" end className={linkClass}>
            New session
          </NavLink>
          <NavLink to="/library" className={linkClass}>
            Library
          </NavLink>
        </nav>
      </header>
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
      <LoginGate />
    </div>
  );
}
