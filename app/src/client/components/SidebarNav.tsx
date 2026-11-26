import { Bookmark, CheckCircle2, Flame, KeyRound, Settings, Trash2 } from "lucide-react";

export type Tab = "inbox" | "saved" | "archived" | "rejected" | "settings";

interface Props {
  tab: Tab;
  counts: {
    inbox: number;
    saved: number;
    archived: number;
    rejected: number;
    pending_ai: number;
  };
  onChange: (tab: Tab) => void;
  onOpenSync: () => void;
  synced: boolean;
}

const ITEMS: Array<{ id: Tab; label: string; short: string; icon: typeof Flame; countKey?: keyof Props["counts"] }> = [
  { id: "inbox", label: "Новое", short: "Новое", icon: Flame, countKey: "inbox" },
  { id: "saved", label: "Отложено", short: "Отложено", icon: Bookmark, countKey: "saved" },
  { id: "archived", label: "Архив", short: "Архив", icon: CheckCircle2, countKey: "archived" },
  { id: "rejected", label: "Мусор ИИ", short: "Мусор", icon: Trash2, countKey: "rejected" },
  { id: "settings", label: "Настройки", short: "Настройки", icon: Settings },
];

export default function SidebarNav({ tab, counts, onChange, onOpenSync, synced }: Props) {
  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-ink-800 bg-ink-900/60 px-3 py-5 md:flex">
        <div className="mb-6 flex items-center gap-2.5 px-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400 to-blue-500">
            <Flame className="h-4 w-4 text-ink-950" strokeWidth={2.5} />
          </div>
          <span className="text-[15px] font-semibold tracking-tight text-white">NewsRecoms</span>
        </div>

        <nav className="flex flex-col gap-1">
          {ITEMS.map(({ id, label, icon: Icon, countKey }) => {
            const active = tab === id;
            const count = countKey ? counts[countKey] : 0;
            return (
              <button
                key={id}
                onClick={() => onChange(id)}
                className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-[14px] transition ${
                  active ? "bg-ink-800 text-white" : "text-slate-400 hover:bg-ink-850 hover:text-slate-200"
                }`}
              >
                <Icon className={`h-4 w-4 ${active ? "text-emerald-400" : "text-slate-500"}`} />
                <span className="flex-1 text-left">{label}</span>
                {countKey && count > 0 && (
                  <span
                    className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${
                      active ? "bg-emerald-500/15 text-emerald-300" : "bg-ink-700 text-slate-400"
                    }`}
                  >
                    {count > 999 ? "999+" : count}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="mt-auto pt-4">
          <button
            onClick={onOpenSync}
            className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-[14px] transition hover:bg-ink-850 ${
              synced ? "text-emerald-400/80 hover:text-emerald-300" : "text-slate-500 hover:text-slate-300"
            }`}
            title={synced ? "Синхронизация подключена" : "Подключить синхронизацию"}
          >
            <KeyRound className="h-4 w-4" />
            {synced ? "Синхронизация" : "Подключить"}
          </button>
        </div>
      </aside>

      {/* Mobile bottom tab bar */}
      <nav className="safe-bottom fixed bottom-0 left-0 right-0 z-30 flex border-t border-ink-800 bg-ink-900/95 backdrop-blur md:hidden">
        {ITEMS.map(({ id, short, icon: Icon, countKey }) => {
          const active = tab === id;
          const count = countKey ? counts[countKey] : 0;
          return (
            <button
              key={id}
              onClick={() => onChange(id)}
              className={`relative flex flex-1 flex-col items-center gap-1 py-2.5 text-[10px] transition ${
                active ? "text-emerald-400" : "text-slate-500"
              }`}
            >
              <Icon className="h-5 w-5" />
              <span>{short}</span>
              {countKey && count > 0 && (
                <span className="absolute right-1/2 top-1 translate-x-3.5 rounded-full bg-emerald-500 px-1.5 py-px text-[9px] font-bold leading-tight text-ink-950">
                  {count > 99 ? "99+" : count}
                </span>
              )}
            </button>
          );
        })}
      </nav>
    </>
  );
}
