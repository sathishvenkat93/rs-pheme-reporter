import { ChatPane } from "../features/chat/ChatPane";
import { CanvasPane } from "../features/dashboard/CanvasPane";

/** Two-pane shell: chat on the left, canvas on the right. */
export function Workspace() {
  return (
    <div className="grid h-full grid-cols-[minmax(320px,420px)_1fr]">
      <ChatPane />
      <CanvasPane />
    </div>
  );
}
