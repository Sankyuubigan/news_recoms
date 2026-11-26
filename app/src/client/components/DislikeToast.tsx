import { useEffect } from "react";
import { Undo2, X } from "lucide-react";

export interface DislikeState {
  itemId: string;
  title: string;
}

interface Props {
  state: DislikeState | null;
  onReason: (reason: string) => void;
  onUndo: () => void;
  onClose: () => void;
}

const REASONS = ["Реклама", "Кликбейт", "Не моя тема", "Повтор"];

export default function DislikeToast({ state, onReason, onUndo, onClose }: Props) {
  useEffect(() => {
    if (!state) return;
    const t = setTimeout(onClose, 5000);
    return () => clearTimeout(t);
  }, [state, onClose]);

  if (!state) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-40 flex justify-center px-4 md:bottom-6">
      <div className="pf-slide-up pointer-events-auto flex w-full max-w-lg flex-col gap-2 rounded-xl border border-ink-700 bg-ink-850/95 p-3 shadow-2xl backdrop-blur">
        <div className="flex items-start gap-2">
          <p className="flex-1 text-[12.5px] leading-snug text-slate-300">
            <span className="text-slate-500">Скрыто: </span>
            <span className="line-clamp-1">{state.title}</span>
          </p>
          <button onClick={onClose} className="rounded p-0.5 text-slate-500 hover:text-slate-300">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11.5px] text-slate-500">Почему?</span>
          {REASONS.map((r) => (
            <button
              key={r}
              onClick={() => onReason(r)}
              className="rounded-md bg-ink-800 px-2 py-1 text-[11.5px] text-slate-300 transition hover:bg-ink-700 hover:text-white"
            >
              {r}
            </button>
          ))}
          <button
            onClick={onUndo}
            className="ml-auto flex items-center gap-1 rounded-md px-2 py-1 text-[11.5px] text-slate-400 transition hover:bg-ink-800 hover:text-slate-200"
          >
            <Undo2 className="h-3.5 w-3.5" />
            Отменить
          </button>
        </div>
      </div>
    </div>
  );
}
