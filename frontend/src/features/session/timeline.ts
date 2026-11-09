// Pure reducer for the chat timeline and live session signals (SSE events + local actions).

export type TimelineItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string }
  | { kind: "diff"; id: string; changes: string[] }
  | { kind: "progress"; id: string; text: string }
  | { kind: "error"; id: string; text: string };

export interface EditPreview {
  base_version: number;
  note: string;
  widgets: Array<{ id: string; before: unknown; after: unknown }>;
  changes: unknown[];
}

export interface LiveState {
  items: TimelineItem[];
  streaming: string; // assistant text arriving while a reply is pending
  pending: boolean;
  confirm: { plan_version: number; charts: number } | null;
  preview: EditPreview | null;
  progress: string | null;
}

export const initialLive: LiveState = { items: [], streaming: "", pending: false, confirm: null, preview: null, progress: null };

export type LiveAction =
  | { type: "history"; items: TimelineItem[] }
  | { type: "sent"; id: string; text: string }
  | { type: "replied"; id: string; text: string }
  | { type: "failed"; id: string; text: string }
  | { type: "event"; event: string; data: Record<string, unknown> }
  | { type: "clearConfirm" }
  | { type: "clearPreview" };

const PROGRESS: Record<string, string> = {
  profiling: "Profiling the data…",
  parse_chart_request: "Reading your request…",
  check_feasibility: "Checking it against the data…",
  update_plan: "Updating the plan…",
  render_preview: "Drawing a preview…",
  resolve_widget: "Finding the widget…",
  patch_dashboard: "Preparing the edit…",
  build_dashboard: "Building the dashboard…",
};

export function reduce(state: LiveState, action: LiveAction): LiveState {
  switch (action.type) {
    case "history":
      return { ...state, items: action.items };
    case "sent":
      return { ...state, pending: true, streaming: "", items: [...state.items, { kind: "user", id: action.id, text: action.text }] };
    case "replied":
      return { ...state, pending: false, streaming: "", progress: null,
               items: [...state.items, { kind: "assistant", id: action.id, text: action.text }] };
    case "failed":
      return { ...state, pending: false, streaming: "", progress: null,
               items: [...state.items, { kind: "error", id: action.id, text: action.text }] };
    case "clearConfirm":
      return { ...state, confirm: null };
    case "clearPreview":
      return { ...state, preview: null };
    case "event":
      return onEvent(state, action.event, action.data);
  }
}

function onEvent(state: LiveState, event: string, data: Record<string, unknown>): LiveState {
  switch (event) {
    case "assistant_delta":
      return state.pending ? { ...state, streaming: state.streaming + String(data.text ?? "") } : state;
    case "tool_progress": {
      const key = String(data.tool ?? data.step ?? "");
      return { ...state, progress: PROGRESS[key] ?? "Working…" };
    }
    case "plan_diff": {
      const changes = (data.changes as string[] | undefined) ?? [];
      if (!changes.length || (changes.length === 1 && changes[0] === "No changes")) return state;
      return { ...state, items: [...state.items, { kind: "diff", id: `diff-${String(data.version)}`, changes }] };
    }
    case "approval_confirm":
      return { ...state, confirm: { plan_version: Number(data.plan_version), charts: Number(data.charts) } };
    case "edit_preview":
      return { ...state, preview: data as unknown as EditPreview };
    case "state":
      return data.state === "APPROVED" ? { ...state, confirm: null } : state;
    case "dashboard":
      return { ...state, preview: null };
    default:
      return state;
  }
}

/** Query keys an event makes stale. */
export function staleKeys(event: string): string[] {
  switch (event) {
    case "plan_diff":
    case "options":
    case "decision":
    case "approval_card":
      return ["plan", "session"];
    case "state":
      return ["session", "plan"];
    case "dashboard":
      return ["session", "dashboard", "versions", "data", "library"];
    default:
      return [];
  }
}
