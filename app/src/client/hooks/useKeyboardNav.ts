import { useEffect, useRef } from "react";

export type KeyAction = "next" | "prev" | "archive" | "save" | "like" | "dislike" | "open";

const KEY_MAP: Record<string, KeyAction> = {
  j: "next",
  k: "prev",
  e: "archive",
  s: "save",
  l: "like",
  d: "dislike",
  o: "open",
};

export function useKeyboardNav(handler: (action: KeyAction) => void, enabled = true) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!enabled) return;

    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      const action = KEY_MAP[e.key.toLowerCase()];
      if (!action) return;

      e.preventDefault();
      handlerRef.current(action);
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
