import { useEffect, useRef, useState } from "react";
import { Send, ThumbsDown, ThumbsUp, Undo2, X } from "lucide-react";
import type { ItemStatus } from "../api/types";

export interface FeedbackState {
  itemId: string;
  title: string;
  kind: "like" | "dislike";
  /**
   * Статус до реакции. Нужен «Отменить»: лайк из входящих уводит карточку
   * в архив, и отмена должна вернуть её ровно туда, где она была, а не
   * всегда в inbox (иначе карточка из архива «всплывёт» в ленту).
   */
  prevStatus?: ItemStatus;
}

interface Props {
  state: FeedbackState | null;
  /** Готовая причина — из пресета или введённая руками. */
  onReason: (reason: string) => void;
  onUndo: () => void;
  onClose: () => void;
}

/**
 * Пресеты — быстрый путь. Текстовое поле — когда одним словом не объяснить:
 * learner получает причину дословно и учится на ней (`feedback_reason`
 * уходит в `build_profile_user_prompt` как поле `reason`).
 */
const PRESETS: Record<FeedbackState["kind"], string[]> = {
  like: ["Глубокий разбор", "Моя тема", "Полезные цифры", "Хороший гайд"],
  dislike: ["Реклама", "Кликбейт", "Не моя тема", "Повтор"],
};

const AUTO_CLOSE_MS = 9000;

export default function FeedbackToast({ state, onReason, onUndo, onClose }: Props) {
  const [text, setText] = useState("");
  const [leftMs, setLeftMs] = useState(AUTO_CLOSE_MS);

  // onClose приходит инлайновой стрелкой из App и меняет идентичность на каждом
  // рендере. Держим её в ref, иначе эффект таймера перезапускался бы и отсчёт
  // каждый раз начинался заново.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Перешли к другой карточке — начинаем с чистого поля.
  useEffect(() => {
    setText("");
  }, [state?.itemId]);

  // Автозакрытие с видимым обратным отсчётом. Пока в поле есть текст — стоим
  // на паузе: иначе тост исчезнет ровно в середине объяснения и причина
  // потеряется. Снял текст — отсчёт начинается заново с полного интервала.
  useEffect(() => {
    if (!state) {
      setLeftMs(AUTO_CLOSE_MS);
      return;
    }
    if (text.trim()) return;

    const deadline = Date.now() + AUTO_CLOSE_MS;
    setLeftMs(AUTO_CLOSE_MS);
    const id = window.setInterval(() => {
      const rest = deadline - Date.now();
      if (rest <= 0) {
        window.clearInterval(id);
        setLeftMs(0);
        onCloseRef.current();
        return;
      }
      setLeftMs(rest);
    }, 100);
    return () => window.clearInterval(id);
  }, [state, text]);

  if (!state) return null;

  const isLike = state.kind === "like";
  const presets = PRESETS[state.kind];
  const paused = Boolean(text.trim());
  const secondsLeft = Math.ceil(leftMs / 1000);

  const submit = () => {
    const v = text.trim();
    if (v) onReason(v);
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-40 flex justify-center px-4 md:bottom-6">
      <div className="pf-slide-up pointer-events-auto flex w-full max-w-lg flex-col gap-2 overflow-hidden rounded-xl border border-ink-700 bg-ink-850/95 p-3 shadow-2xl backdrop-blur">
        <div className="flex items-start gap-2">
          <span className={`mt-0.5 shrink-0 ${isLike ? "text-emerald-400" : "text-rose-400"}`}>
            {isLike ? <ThumbsUp className="h-3.5 w-3.5" /> : <ThumbsDown className="h-3.5 w-3.5" />}
          </span>
          <p className="flex-1 text-[12.5px] leading-snug text-slate-300">
            <span className="text-slate-500">{isLike ? "Понравилось: " : "Скрыто: "}</span>
            <span className="line-clamp-1">{state.title}</span>
          </p>
          <span
            className="shrink-0 self-center tabular-nums text-[11px] text-slate-500"
            title={paused ? "Отсчёт на паузе, пока ты пишешь" : "Через сколько тост закроется"}
          >
            {paused ? "пауза" : `${secondsLeft} с`}
          </span>
          <button onClick={onClose} className="rounded p-0.5 text-slate-500 hover:text-slate-300">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11.5px] text-slate-500">Почему?</span>
          {presets.map((r) => (
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

        <div className="flex items-center gap-1.5">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
              if (e.key === "Escape") onClose();
            }}
            maxLength={200}
            placeholder={isLike ? "Своими словами, что зацепило…" : "Своими словами, что не так…"}
            className="min-w-0 flex-1 rounded-md border border-ink-800 bg-ink-900 px-2 py-1.5 text-[12px] text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-emerald-500/50"
          />
          <button
            onClick={submit}
            disabled={!text.trim()}
            className="flex shrink-0 items-center gap-1 rounded-md bg-emerald-500/15 px-2 py-1.5 text-[11.5px] text-emerald-300 transition enabled:hover:bg-emerald-500/25 disabled:opacity-35"
          >
            <Send className="h-3 w-3" />
            Сохранить
          </button>
        </div>

        {/* Полоса прогресса: показывает, что тост закроется сам, и сколько осталось. */}
        <div className="-mx-3 -mb-3 mt-0.5 h-0.5 bg-ink-800">
          <div
            className={`h-full transition-[width] duration-100 ease-linear ${
              isLike ? "bg-emerald-500/60" : "bg-rose-500/60"
            }`}
            style={{ width: `${(leftMs / AUTO_CLOSE_MS) * 100}%` }}
          />
        </div>
      </div>
    </div>
  );
}
