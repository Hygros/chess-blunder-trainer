import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/preact';
import { useEffect, useRef } from 'preact/hooks';
import { useDrag } from '../../src/trainer/hooks/useDrag';
import { STORAGE_KEYS } from '../../src/shared/storage-keys';

interface DragHarnessProps {
  onReady?: (restorePosition: () => void) => void;
  active?: boolean;
}

function DragHarness({ onReady, active = true }: DragHarnessProps): preact.JSX.Element {
  const cardRef = useRef<HTMLDivElement>(null);
  const { handleRef, restorePosition } = useDrag(cardRef, active);

  useEffect(() => {
    onReady?.(restorePosition);
  }, [onReady, restorePosition]);

  return (
    <div ref={cardRef} class="board-result-card">
      <div ref={handleRef} class="board-result-drag-handle" />
      <div class="board-result-inner" />
    </div>
  );
}

function DragVisibilityHarness({ visible, onReady }: { visible: boolean; onReady?: (restorePosition: () => void) => void }): preact.JSX.Element | null {
  const cardRef = useRef<HTMLDivElement>(null);
  const { handleRef, restorePosition } = useDrag(cardRef, visible);

  useEffect(() => {
    onReady?.(restorePosition);
  }, [onReady, restorePosition]);

  if (!visible) return null;

  return (
    <div ref={cardRef} class="board-result-card">
      <div ref={handleRef} class="board-result-drag-handle" />
      <div class="board-result-inner" />
    </div>
  );
}

interface ResizeObserverInstance {
  observe: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  trigger: () => void;
}

describe('useDrag', () => {
  const resizeObservers: ResizeObserverInstance[] = [];

  beforeEach(() => {
    localStorage.clear();
    resizeObservers.length = 0;

    class MockResizeObserver {
      readonly observe = vi.fn();
      readonly disconnect = vi.fn();

      constructor(private readonly callback: ResizeObserverCallback) {
        resizeObservers.push({
          observe: this.observe,
          disconnect: this.disconnect,
          trigger: () => {
            this.callback([], this as unknown as ResizeObserver);
          },
        });
      }
    }

    vi.stubGlobal('ResizeObserver', MockResizeObserver);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('restores stored position and size', () => {
    localStorage.setItem(STORAGE_KEYS.trainerResultCardPos, JSON.stringify({ leftPx: 120, topPx: 80 }));
    localStorage.setItem(STORAGE_KEYS.trainerResultCardSize, JSON.stringify({ widthPx: 420, heightPx: 260 }));

    let restorePosition: (() => void) | null = null;
    const { container } = render(<DragHarness onReady={(restore) => { restorePosition = restore; }} />);

    const card = container.querySelector('.board-result-card') as HTMLDivElement;
    const inner = container.querySelector('.board-result-inner') as HTMLDivElement;

    Object.defineProperty(card, 'offsetWidth', { configurable: true, get: () => 340 });
    Object.defineProperty(card, 'offsetHeight', { configurable: true, get: () => 220 });

    restorePosition?.();

    expect(card.style.left).toBe('120px');
    expect(card.style.top).toBe('80px');
    expect(card.style.right).toBe('auto');
    expect(card.style.bottom).toBe('auto');
    expect(inner.style.width).toBe('420px');
    expect(inner.style.height).toBe('260px');
  });

  it('persists position when drag ends', () => {
    const { container } = render(<DragHarness />);
    const card = container.querySelector('.board-result-card') as HTMLDivElement;
    const handle = container.querySelector('.board-result-drag-handle') as HTMLDivElement;

    Object.defineProperty(handle, 'setPointerCapture', { configurable: true, value: vi.fn() });
    Object.defineProperty(handle, 'releasePointerCapture', { configurable: true, value: vi.fn() });
    card.getBoundingClientRect = vi.fn(() => ({ left: 42, top: 64, width: 340, height: 220 } as DOMRect));
    Object.defineProperty(card, 'offsetWidth', { configurable: true, get: () => 340 });
    Object.defineProperty(card, 'offsetHeight', { configurable: true, get: () => 220 });

    const pointerDown = new Event('pointerdown', { bubbles: true, cancelable: true }) as PointerEvent;
    Object.defineProperty(pointerDown, 'clientX', { value: 10 });
    Object.defineProperty(pointerDown, 'clientY', { value: 10 });
    Object.defineProperty(pointerDown, 'pointerId', { value: 1 });
    handle.dispatchEvent(pointerDown);

    const pointerMove = new Event('pointermove', { bubbles: true, cancelable: true }) as PointerEvent;
    Object.defineProperty(pointerMove, 'clientX', { value: 20 });
    Object.defineProperty(pointerMove, 'clientY', { value: 25 });
    Object.defineProperty(pointerMove, 'pointerId', { value: 1 });
    handle.dispatchEvent(pointerMove);

    const pointerUp = new Event('pointerup', { bubbles: true, cancelable: true }) as PointerEvent;
    Object.defineProperty(pointerUp, 'pointerId', { value: 1 });
    handle.dispatchEvent(pointerUp);

    const stored = localStorage.getItem(STORAGE_KEYS.trainerResultCardPos);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored as string)).toEqual({ leftPx: 42, topPx: 64 });
  });

  it('persists size via ResizeObserver callback', () => {
    let restorePosition: (() => void) | null = null;
    const { container } = render(<DragHarness onReady={(restore) => { restorePosition = restore; }} />);
    const inner = container.querySelector('.board-result-inner') as HTMLDivElement;

    restorePosition?.();

    inner.getBoundingClientRect = vi.fn(() => ({
      left: 0,
      top: 0,
      width: 500,
      height: 300,
      right: 500,
      bottom: 300,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect));

    expect(resizeObservers.length).toBeGreaterThan(0);
    resizeObservers[0].trigger();

    const stored = localStorage.getItem(STORAGE_KEYS.trainerResultCardSize);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored as string)).toEqual({ widthPx: 500, heightPx: 300 });
  });

  it('does not overwrite stored size before restore runs', () => {
    localStorage.setItem(STORAGE_KEYS.trainerResultCardSize, JSON.stringify({ widthPx: 460, heightPx: 280 }));

    const { container } = render(<DragHarness />);
    const inner = container.querySelector('.board-result-inner') as HTMLDivElement;

    inner.getBoundingClientRect = vi.fn(() => ({
      left: 0,
      top: 0,
      width: 340,
      height: 200,
      right: 340,
      bottom: 200,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect));

    expect(resizeObservers.length).toBeGreaterThan(0);
    resizeObservers[0].trigger();

    expect(localStorage.getItem(STORAGE_KEYS.trainerResultCardSize)).toBe(JSON.stringify({ widthPx: 460, heightPx: 280 }));
  });

  it('ignores malformed storage without throwing', () => {
    localStorage.setItem(STORAGE_KEYS.trainerResultCardPos, '{bad-json');
    localStorage.setItem(STORAGE_KEYS.trainerResultCardSize, '{bad-json');

    let restorePosition: (() => void) | null = null;
    const { container } = render(<DragHarness onReady={(restore) => { restorePosition = restore; }} />);

    const card = container.querySelector('.board-result-card') as HTMLDivElement;
    const inner = container.querySelector('.board-result-inner') as HTMLDivElement;
    Object.defineProperty(card, 'offsetWidth', { configurable: true, get: () => 340 });
    Object.defineProperty(card, 'offsetHeight', { configurable: true, get: () => 220 });

    expect(() => restorePosition?.()).not.toThrow();
    expect(card.style.left).toBe('');
    expect(card.style.top).toBe('');
    expect(inner.style.width).toBe('');
    expect(inner.style.height).toBe('');
  });

  it('attaches size observer when card becomes visible later', () => {
    let restorePosition: (() => void) | null = null;
    const { rerender, container } = render(<DragVisibilityHarness visible={false} onReady={(restore) => { restorePosition = restore; }} />);

    expect(resizeObservers.length).toBe(0);

    rerender(<DragVisibilityHarness visible={true} onReady={(restore) => { restorePosition = restore; }} />);
    expect(resizeObservers.length).toBe(1);

    const inner = container.querySelector('.board-result-inner') as HTMLDivElement;
    inner.getBoundingClientRect = vi.fn(() => ({
      left: 0,
      top: 0,
      width: 480,
      height: 320,
      right: 480,
      bottom: 320,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect));

    restorePosition?.();
    resizeObservers[0].trigger();

    expect(localStorage.getItem(STORAGE_KEYS.trainerResultCardSize)).toBe(JSON.stringify({ widthPx: 480, heightPx: 320 }));
  });
});
