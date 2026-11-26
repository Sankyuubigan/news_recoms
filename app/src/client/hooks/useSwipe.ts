import { useRef, useState } from "react";

interface SwipeHandlers {
  onSwipeRight?: () => void;
  onSwipeLeft?: () => void;
}

const THRESHOLD = 80;
const MAX_OFFSET = 140;

/**
 * Свайпы по карточке: вправо = просмотрено, влево = дизлайк.
 * Не мешает вертикальному скроллу: реагирует только при явно горизонтальном движении.
 */
export function useSwipe({ onSwipeRight, onSwipeLeft }: SwipeHandlers) {
  const start = useRef<{ x: number; y: number; locked: boolean } | null>(null);
  const [offsetX, setOffsetX] = useState(0);
  const [active, setActive] = useState(false);

  const onTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length !== 1) return;
    start.current = { x: e.touches[0].clientX, y: e.touches[0].clientY, locked: false };
  };

  const onTouchMove = (e: React.TouchEvent) => {
    const s = start.current;
    if (!s) return;
    const dx = e.touches[0].clientX - s.x;
    const dy = e.touches[0].clientY - s.y;

    if (!s.locked) {
      if (Math.abs(dy) > Math.abs(dx)) {
        // вертикальный скролл — отдаём браузеру
        start.current = null;
        return;
      }
      if (Math.abs(dx) > 12) {
        s.locked = true;
        setActive(true);
      } else {
        return;
      }
    }

    setOffsetX(Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET, dx)));
  };

  const finish = () => {
    const dx = offsetX;
    start.current = null;
    setActive(false);
    setOffsetX(0);

    if (dx >= THRESHOLD) onSwipeRight?.();
    else if (dx <= -THRESHOLD) onSwipeLeft?.();
  };

  const onTouchEnd = () => {
    if (!start.current) {
      setOffsetX(0);
      setActive(false);
      return;
    }
    finish();
  };

  return {
    offsetX,
    active,
    bind: {
      onTouchStart,
      onTouchMove,
      onTouchEnd,
      onTouchCancel: onTouchEnd,
    },
    style: {
      transform: `translateX(${offsetX}px)`,
      transition: active ? "none" : "transform 180ms cubic-bezier(0.16,1,0.3,1)",
    } as React.CSSProperties,
  };
}
