import { useRef, useCallback, useEffect } from 'preact/hooks';
import { STORAGE_KEYS } from '../../shared/storage-keys';

const DRAG_STORAGE_KEY = STORAGE_KEYS.trainerResultCardPos;
const SIZE_STORAGE_KEY = STORAGE_KEYS.trainerResultCardSize;

interface StoredPosition {
  leftPx: number;
  topPx: number;
}

interface StoredSize {
  widthPx: number;
  heightPx: number;
}

function parseStoredPosition(raw: string | null): StoredPosition | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredPosition>;
    if (typeof parsed.leftPx !== 'number' || typeof parsed.topPx !== 'number') return null;
    return { leftPx: parsed.leftPx, topPx: parsed.topPx };
  } catch {
    return null;
  }
}

function parseStoredSize(raw: string | null): StoredSize | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredSize>;
    if (typeof parsed.widthPx !== 'number' || typeof parsed.heightPx !== 'number') return null;
    return { widthPx: parsed.widthPx, heightPx: parsed.heightPx };
  } catch {
    return null;
  }
}

export function useDrag(
  cardRef: preact.RefObject<HTMLDivElement | null>,
  isActive = true,
): {
  handleRef: (el: HTMLDivElement | null) => void;
  restorePosition: () => void;
} {
  const handleElRef = useRef<HTMLDivElement | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const sizePersistenceEnabledRef = useRef(false);
  const draggingRef = useRef(false);
  const startRef = useRef({ x: 0, y: 0, left: 0, top: 0 });

  const saveSize = useCallback(() => {
    if (!sizePersistenceEnabledRef.current) return;
    const card = cardRef.current;
    const inner = card?.querySelector<HTMLDivElement>('.board-result-inner');
    if (!inner) return;

    const rect = inner.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    localStorage.setItem(SIZE_STORAGE_KEY, JSON.stringify({
      widthPx: rect.width,
      heightPx: rect.height,
    }));
  }, [cardRef]);

  const restorePosition = useCallback(() => {
    const card = cardRef.current;
    if (!card) return;

    sizePersistenceEnabledRef.current = false;

    const inner = card.querySelector<HTMLDivElement>('.board-result-inner');
    const size = parseStoredSize(localStorage.getItem(SIZE_STORAGE_KEY));
    if (inner && size) {
      inner.style.width = `${String(Math.max(220, size.widthPx))}px`;
      inner.style.height = `${String(Math.max(120, size.heightPx))}px`;
    }

    sizePersistenceEnabledRef.current = true;

    const pos = parseStoredPosition(localStorage.getItem(DRAG_STORAGE_KEY));
    if (!pos) return;

    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const maxLeft = Math.max(0, vw - card.offsetWidth);
    const maxTop = Math.max(0, vh - card.offsetHeight);
    const left = Math.max(0, Math.min(pos.leftPx, maxLeft));
    const top = Math.max(0, Math.min(pos.topPx, maxTop));
    card.style.left = `${String(left)}px`;
    card.style.top = `${String(top)}px`;
    card.style.right = 'auto';
    card.style.bottom = 'auto';
  }, [cardRef]);

  const savePosition = useCallback(() => {
    const card = cardRef.current;
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const pos = { leftPx: rect.left, topPx: rect.top };
    localStorage.setItem(DRAG_STORAGE_KEY, JSON.stringify(pos));
  }, [cardRef]);

  const onPointerDown = useCallback((e: PointerEvent) => {
    e.preventDefault();
    const card = cardRef.current;
    const handle = handleElRef.current;
    if (!card || !handle) return;

    draggingRef.current = true;
    card.classList.add('dragging');
    const rect = card.getBoundingClientRect();
    startRef.current = { x: e.clientX, y: e.clientY, left: rect.left, top: rect.top };
    handle.setPointerCapture(e.pointerId);
  }, [cardRef]);

  const onPointerMove = useCallback((e: PointerEvent) => {
    if (!draggingRef.current) return;
    const card = cardRef.current;
    if (!card) return;
    const s = startRef.current;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let newLeft = s.left + (e.clientX - s.x);
    let newTop = s.top + (e.clientY - s.y);
    newLeft = Math.max(0, Math.min(newLeft, vw - card.offsetWidth));
    newTop = Math.max(0, Math.min(newTop, vh - card.offsetHeight));
    card.style.left = `${String(newLeft)}px`;
    card.style.top = `${String(newTop)}px`;
    card.style.right = 'auto';
    card.style.bottom = 'auto';
  }, [cardRef]);

  const onPointerUp = useCallback((e: PointerEvent) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    cardRef.current?.classList.remove('dragging');
    handleElRef.current?.releasePointerCapture(e.pointerId);
    savePosition();
  }, [cardRef, savePosition]);

  const onPointerCancelOrCaptureLoss = useCallback(() => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    cardRef.current?.classList.remove('dragging');
    savePosition();
  }, [cardRef, savePosition]);

  const handleRef = useCallback((el: HTMLDivElement | null) => {
    const prev = handleElRef.current;
    if (prev) {
      prev.removeEventListener('pointerdown', onPointerDown);
      prev.removeEventListener('pointermove', onPointerMove);
      prev.removeEventListener('pointerup', onPointerUp);
      prev.removeEventListener('pointercancel', onPointerCancelOrCaptureLoss);
      prev.removeEventListener('lostpointercapture', onPointerCancelOrCaptureLoss);
    }
    handleElRef.current = el;
    if (el) {
      el.addEventListener('pointerdown', onPointerDown);
      el.addEventListener('pointermove', onPointerMove);
      el.addEventListener('pointerup', onPointerUp);
      el.addEventListener('pointercancel', onPointerCancelOrCaptureLoss);
      el.addEventListener('lostpointercapture', onPointerCancelOrCaptureLoss);
    }
  }, [onPointerDown, onPointerMove, onPointerUp, onPointerCancelOrCaptureLoss]);

  useEffect(() => {
    if (!isActive) {
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
      sizePersistenceEnabledRef.current = false;
      return;
    }

    const card = cardRef.current;
    const inner = card?.querySelector<HTMLDivElement>('.board-result-inner');
    if (!inner) return;

    sizePersistenceEnabledRef.current = false;

    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => {
        saveSize();
      });
      observer.observe(inner);
      resizeObserverRef.current = observer;

      return () => {
        observer.disconnect();
        resizeObserverRef.current = null;
        sizePersistenceEnabledRef.current = false;
      };
    }

    const onWindowResize = () => {
      saveSize();
    };

    window.addEventListener('resize', onWindowResize);
    return () => {
      window.removeEventListener('resize', onWindowResize);
      sizePersistenceEnabledRef.current = false;
    };
  }, [cardRef, saveSize, isActive]);

  useEffect(() => {
    return () => {
      const el = handleElRef.current;
      if (el) {
        el.removeEventListener('pointerdown', onPointerDown);
        el.removeEventListener('pointermove', onPointerMove);
        el.removeEventListener('pointerup', onPointerUp);
        el.removeEventListener('pointercancel', onPointerCancelOrCaptureLoss);
        el.removeEventListener('lostpointercapture', onPointerCancelOrCaptureLoss);
      }
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
      sizePersistenceEnabledRef.current = false;
    };
  }, [onPointerDown, onPointerMove, onPointerUp, onPointerCancelOrCaptureLoss]);

  return { handleRef, restorePosition };
}
