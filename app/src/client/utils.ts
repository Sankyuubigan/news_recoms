export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));

  if (diffSec < 60) return "только что";
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min} мин назад`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "вчера";
  if (days < 30) return `${days} дн назад`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} мес назад`;
  return `${Math.floor(months / 12)} г назад`;
}

export interface ScoreStyle {
  label: string;
  badge: string;
  dot: string;
}

export function scoreStyle(score: number | null | undefined): ScoreStyle {
  const s = typeof score === "number" ? score : 0;
  if (s >= 9) {
    return {
      label: s.toFixed(1),
      badge: "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30",
      dot: "bg-emerald-400",
    };
  }
  if (s >= 7.5) {
    return {
      label: s.toFixed(1),
      badge: "bg-blue-500/15 text-blue-300 ring-1 ring-blue-500/30",
      dot: "bg-blue-400",
    };
  }
  if (s >= 6.5) {
    return {
      label: s.toFixed(1),
      badge: "bg-slate-500/15 text-slate-300 ring-1 ring-slate-500/30",
      dot: "bg-slate-400",
    };
  }
  return {
    label: s.toFixed(1),
    badge: "bg-rose-500/10 text-rose-300/80 ring-1 ring-rose-500/25",
    dot: "bg-rose-400/70",
  };
}

export function sourceLabel(type: "telegram" | "rss" | "youtube"): string {
  if (type === "telegram") return "TG";
  if (type === "youtube") return "YT";
  return "WEB";
}

export function sourceTint(type: "telegram" | "rss" | "youtube"): string {
  if (type === "telegram") return "bg-sky-500/15 text-sky-300";
  if (type === "youtube") return "bg-red-500/15 text-red-300";
  return "bg-amber-500/15 text-amber-300";
}

export function isEnglish(item: { original_lang: string | null }): boolean {
  return (item.original_lang ?? "").toLowerCase().startsWith("en");
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
