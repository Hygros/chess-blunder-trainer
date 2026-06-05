import { useEffect, useRef } from 'preact/hooks';
import { Chessground } from '@vendor/chessground';
import type { HighlightMap } from '../../shared/highlights';
import type { Arrow } from '../hooks/useBoardState';

interface ChessgroundShape {
  orig: string;
  dest?: string;
  brush?: string;
}

interface ChessgroundApi {
  set(config: Record<string, unknown>): void;
  setAutoShapes(shapes: ChessgroundShape[]): void;
  destroy(): void;
}

interface BoardProps {
  fen: string;
  orientation: 'white' | 'black';
  interactive: boolean;
  movableColor?: 'white' | 'black' | 'both';
  coordinates: boolean;
  highlights: HighlightMap;
  arrows: Arrow[];
  gameRef: preact.RefObject<ChessInstance | null>;
  onMove: (orig: string, dest: string, move: { san: string; from: string; to: string; promotion?: string }) => void;
  animateFrom?: { fen: string; from: string; to: string; onComplete: () => void } | null;
  moveCount?: number;
}

function buildDests(game: ChessInstance): Map<string, string[]> {
  const dests = new Map<string, string[]>();
  const files = 'abcdefgh';
  for (let f = 0; f < 8; f++) {
    for (let r = 1; r <= 8; r++) {
      const sq = (files[f] ?? '') + String(r);
      const moves = game.moves({ square: sq, verbose: true });
      if (moves.length > 0) {
        dests.set(sq, moves.map(m => m.to));
      }
    }
  }
  return dests;
}

export function Board({
  fen, orientation, interactive, movableColor, coordinates, highlights,
  arrows, gameRef, onMove, animateFrom, moveCount,
}: BoardProps): preact.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const cgRef = useRef<ChessgroundApi | null>(null);
  const onMoveRef = useRef(onMove);
  onMoveRef.current = onMove;
  const orientationRef = useRef(orientation);
  orientationRef.current = orientation;
  const movableColorRef = useRef(movableColor);
  movableColorRef.current = movableColor;
  const animPlayedRef = useRef<object | null>(null);

  // Mount Chessground once
  useEffect(() => {
    const el = containerRef.current;
    if (!el || cgRef.current) return;

    el.innerHTML = '';
    const game = gameRef.current;
    const turnColor = game?.turn() === 'w' ? 'white' : 'black';

    const cg = Chessground(el, {
      fen,
      orientation,
      turnColor,
      coordinates: true,
      ranksPosition: 'left',
      animation: { enabled: true, duration: 150 },
      movable: {
        free: false,
        color: interactive ? (movableColor ?? orientation) : undefined,
        dests: game && interactive ? buildDests(game) : new Map(),
        showDests: true,
        events: {
          after: (orig: string, dest: string) => {
            const g = gameRef.current;
            if (!g) {
              // No game — resync board to prevent stuck state
              cg.set({ movable: { dests: new Map() } });
              return;
            }
            const move = g.move({ from: orig, to: dest, promotion: 'q' });
            if (!move) {
              // Move rejected by chess.js — game state is out of sync with
              // what Chessground displayed. Resync board to actual game state
              // to prevent a permanently frozen board (dests was already
              // cleared by Chessground's internal move handler).
              const turnCol = g.turn() === 'w' ? 'white' : 'black';
              const color = movableColorRef.current ?? orientationRef.current;
              cg.set({
                fen: g.fen(),
                turnColor: turnCol,
                movable: { color, dests: buildDests(g) },
              });
              return;
            }

            const turnCol = g.turn() === 'w' ? 'white' : 'black';
            const color = movableColorRef.current ?? orientationRef.current;
            cg.set({
              fen: g.fen(),
              turnColor: turnCol,
              movable: {
                color,
                dests: buildDests(g),
              },
              lastMove: [orig, dest],
            });

            onMoveRef.current(orig, dest, move);
          },
        },
      },
      draggable: { enabled: true, showGhost: true },
      highlight: { lastMove: true, check: true },
      premovable: { enabled: false },
      drawable: { enabled: false },
    });
    cgRef.current = cg;

    return () => {
      cg.destroy();
      cgRef.current = null;
    };
  }, []); // mount-only: Chessground initializes once

  // Sync fen + movable when position changes
  useEffect(() => {
    const cg = cgRef.current;
    if (!cg) return;
    const game = gameRef.current;
    const turnColor = game?.turn() === 'w' ? 'white' : 'black';
    cg.set({
      fen,
      turnColor,
      movable: {
        color: interactive ? (movableColor ?? orientation) : undefined,
        dests: game && interactive ? buildDests(game) : new Map(),
      },
    });
  }, [fen, interactive, orientation, movableColor, moveCount, gameRef]);

  // Sync orientation
  useEffect(() => {
    cgRef.current?.set({ orientation });
  }, [orientation]);

  // Sync coordinates
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.classList.toggle('hide-coords', !coordinates);
  }, [coordinates]);

  // Sync highlights + arrows
  useEffect(() => {
    const cg = cgRef.current;
    if (!cg) return;
    const highlightShapes: ChessgroundShape[] = Array.from(highlights.entries()).map(([square, brush]) => ({
      orig: square,
      brush,
    }));
    const arrowShapes: ChessgroundShape[] = arrows.map(a => ({
      orig: a.from,
      dest: a.to,
      brush: a.color === 'red' ? 'red' : a.color === 'orange' ? 'yellow' : 'green',
    }));
    cg.setAutoShapes([...arrowShapes, ...highlightShapes]);
  }, [highlights, arrows]);

  // Pre-move animation
  useEffect(() => {
    if (!animateFrom) return;
    if (animPlayedRef.current === animateFrom) return;
    const cg = cgRef.current;
    if (!cg) return;
    const game = gameRef.current;
    let t2: ReturnType<typeof setTimeout> | undefined;

    const t1 = setTimeout(() => {
      cg.set({
        animation: { duration: 350 },
        fen,
        lastMove: [animateFrom.from, animateFrom.to],
        turnColor: game?.turn() === 'w' ? 'white' : 'black',
      });

      t2 = setTimeout(() => {
        animPlayedRef.current = animateFrom;
        cg.set({
          animation: { duration: 150 },
          movable: {
            color: orientationRef.current,
            dests: game ? buildDests(game) : new Map(),
          },
        });
        animateFrom.onComplete();
      }, 400);
    }, 400);

    return () => {
      clearTimeout(t1);
      if (t2 !== undefined) clearTimeout(t2);
    };
  }, [animateFrom, fen, gameRef]);

  return <div ref={containerRef} class="cg-wrap" id="board" />;
}
