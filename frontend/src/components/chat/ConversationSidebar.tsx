import { cn } from "@/lib/utils";
import type { Conversation } from "@/types/api";

interface ConversationSidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: ConversationSidebarProps) {
  return (
    <aside className="hidden w-64 shrink-0 flex-col gap-2 overflow-y-auto border-r border-zinc-200 p-3 md:flex dark:border-zinc-800">
      <button
        type="button"
        onClick={onNew}
        className="rounded-xl border border-zinc-300 px-3 py-2 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
      >
        + Cuộc trò chuyện mới
      </button>

      {conversations.length === 0 && (
        <p className="px-1 text-xs text-zinc-500">Chưa có cuộc trò chuyện nào.</p>
      )}

      <ul className="flex flex-col gap-1">
        {conversations.map((c) => (
          <li key={c.id} className="group flex items-center">
            <button
              type="button"
              onClick={() => onSelect(c.id)}
              aria-current={c.id === activeId ? "true" : undefined}
              title={c.title}
              className={cn(
                "flex-1 truncate rounded-lg px-2 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800",
                c.id === activeId && "bg-zinc-100 font-medium dark:bg-zinc-800",
              )}
            >
              {c.title}
            </button>
            <button
              type="button"
              onClick={() => onDelete(c.id)}
              aria-label={`Xoá "${c.title}"`}
              className="px-2 text-zinc-400 opacity-0 group-hover:opacity-100 hover:text-red-600 focus:opacity-100"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
