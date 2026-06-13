import { useCallback, useRef, useContext } from 'preact/hooks';
import { TrainerContext } from '../context';

export function useLinePlayer(
  gameRef: preact.RefObject<ChessInstance | null>,
): {
  playBestMove: () => void;
  navigateLine: (direction: 'forward' | 'back') => void;
  navigateLineTyped: (lineType: 'best' | 'refutation', direction: 'forward' | 'back') => void;
} {
  const { state, dispatch } = useContext(TrainerContext);
  const animGenRef = useRef(0);

  const playBestMove = useCallback(() => {
    if (state.animating) return;
    const puzzle = state.puzzle;
    const game = gameRef.current;
    if (!puzzle || !game) return;

    // Stop continue-play since we're reassigning the game
    if (state.continuePlaying !== 'off') {
      dispatch({ type: 'SET_CONTINUE_PLAYING', mode: 'off' });
    }

    const resetGame = new Chess(puzzle.fen);
    gameRef.current = resetGame;
    dispatch({ type: 'SET_FEN', fen: puzzle.fen });
    dispatch({ type: 'CLEAR_LINE_NAVIGATION' });

    dispatch({ type: 'PUSH_LINE_POSITION', position: { fen: puzzle.fen, moveHistory: [] } });

    if (puzzle.best_line.length > 0) {
      const gen = ++animGenRef.current;
      dispatch({ type: 'SET_ANIMATING', animating: true });

      void (async () => {
        const lineGame = new Chess(puzzle.fen);
        const history: string[] = [];

        for (const san of puzzle.best_line) {
          if (animGenRef.current !== gen) break;
          const result = lineGame.move(san);
          if (!result) break;

          history.push(result.san);
          gameRef.current = lineGame;
          dispatch({ type: 'SET_FEN', fen: lineGame.fen() });
          dispatch({ type: 'PUSH_LINE_POSITION', position: { fen: lineGame.fen(), moveHistory: [...history] } });

          await new Promise(resolve => setTimeout(resolve, 1000));
        }

        if (animGenRef.current === gen) {
          dispatch({ type: 'SET_ANIMATING', animating: false });
        }
      })();
    } else {
      const result = resetGame.move(puzzle.best_move_san);
      if (result) {
        gameRef.current = resetGame;
        dispatch({ type: 'SET_FEN', fen: resetGame.fen() });
        dispatch({ type: 'PUSH_MOVE', san: result.san });
        dispatch({ type: 'PUSH_LINE_POSITION', position: { fen: resetGame.fen(), moveHistory: [result.san] } });
      }
    }
  }, [state.animating, state.puzzle, state.continuePlaying, gameRef, dispatch]);

  const navigateLine = useCallback((direction: 'forward' | 'back') => {
    if (state.animating) return;
    const positions = state.linePositions;
    if (positions.length === 0) return;

    // Stop continue-play since we're reassigning the game
    if (state.continuePlaying !== 'off') {
      dispatch({ type: 'SET_CONTINUE_PLAYING', mode: 'off' });
    }

    const currentIndex = state.lineViewIndex;
    let newIndex: number;

    if (direction === 'back') {
      newIndex = currentIndex <= 0 ? 0 : currentIndex - 1;
    } else {
      newIndex = currentIndex >= positions.length - 1 ? positions.length - 1 : currentIndex + 1;
    }

    if (newIndex === currentIndex) return;

    const pos = positions[newIndex];
    if (!pos) return;

    dispatch({ type: 'SET_LINE_VIEW_INDEX', index: newIndex });
    dispatch({ type: 'SET_FEN', fen: pos.fen });

    const game = new Chess(pos.fen);
    gameRef.current = game;
  }, [state.animating, state.linePositions, state.lineViewIndex, state.continuePlaying, dispatch, gameRef]);

  const navigateLineTyped = useCallback((lineType: 'best' | 'refutation', direction: 'forward' | 'back') => {
    if (state.animating) return;
    const puzzle = state.puzzle;
    if (!puzzle) return;

    // Stop continue-play since we're reassigning the game
    if (state.continuePlaying !== 'off') {
      dispatch({ type: 'SET_CONTINUE_PLAYING', mode: 'off' });
    }

    // Determine the line SANs and starting FEN based on lineType
    let lineSans: string[];
    let startFen: string;

    if (lineType === 'best') {
      lineSans = puzzle.best_line;
      startFen = puzzle.fen;
    } else {
      if (!puzzle.refutation_line_san || puzzle.refutation_line_san.length === 0) return;
      lineSans = [puzzle.blunder_san, ...puzzle.refutation_line_san];
      startFen = puzzle.fen;
    }

    // If switching line type or positions not built yet, build them
    const needsRebuild = state.activeLineType !== lineType || state.linePositions.length === 0;

    let positions = state.linePositions;
    if (needsRebuild) {
      // Build all positions for this line
      const built = [{ fen: startFen, moveHistory: [] as string[] }];
      const lineGame = new Chess(startFen);
      const history: string[] = [];
      for (const san of lineSans) {
        const result = lineGame.move(san);
        if (!result) break;
        history.push(result.san);
        built.push({ fen: lineGame.fen(), moveHistory: [...history] });
      }
      positions = built;

      dispatch({ type: 'CLEAR_LINE_NAVIGATION' });
      for (const pos of built) {
        dispatch({ type: 'PUSH_LINE_POSITION', position: pos });
      }
      dispatch({ type: 'SET_ACTIVE_LINE_TYPE', activeLineType: lineType });

      // Start at position 0 (before any move) when switching line
      const startIdx = direction === 'forward' ? 1 : 0;
      const targetPos = positions[startIdx];
      if (!targetPos) return;
      dispatch({ type: 'SET_LINE_VIEW_INDEX', index: startIdx });
      dispatch({ type: 'SET_FEN', fen: targetPos.fen });
      gameRef.current = new Chess(targetPos.fen);
      return;
    }

    // Already in this line — navigate within it
    const currentIndex = state.lineViewIndex;
    let newIndex: number;
    if (direction === 'back') {
      newIndex = currentIndex <= 0 ? 0 : currentIndex - 1;
    } else {
      newIndex = currentIndex >= positions.length - 1 ? positions.length - 1 : currentIndex + 1;
    }
    if (newIndex === currentIndex) return;

    const pos = positions[newIndex];
    if (!pos) return;

    dispatch({ type: 'SET_LINE_VIEW_INDEX', index: newIndex });
    dispatch({ type: 'SET_FEN', fen: pos.fen });
    gameRef.current = new Chess(pos.fen);
  }, [state.animating, state.puzzle, state.activeLineType, state.linePositions, state.lineViewIndex, state.continuePlaying, gameRef, dispatch]);

  return { playBestMove, navigateLine, navigateLineTyped };
}
