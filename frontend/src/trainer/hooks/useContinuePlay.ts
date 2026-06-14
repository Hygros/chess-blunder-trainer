import { useCallback, useRef, useContext, useEffect, useState } from 'preact/hooks';
import { TrainerContext, type ContinuePlayMode } from '../context';
import { StockfishEngine, spawnStockfishWorker, type EngineUpdate } from '../../shared/engine/stockfish';

const ENGINE_DEPTH = 18;
const MIN_THINK_MS = 700;

export function useContinuePlay(
  gameRef: preact.RefObject<ChessInstance | null>,
): {
  startContinuePlay: (mode: ContinuePlayMode) => void;
  stopContinuePlay: () => void;
  handleContinueMove: () => void;
  refreshEval: () => void;
  engineEvalCp: number | null;
  engineBestMoveUci: string | null;
} {
  const { state, dispatch } = useContext(TrainerContext);
  const engineRef = useRef<StockfishEngine | null>(null);
  const modeRef = useRef<ContinuePlayMode>('off');
  const waitingForMoveRef = useRef(false);
  const latestUpdateRef = useRef<EngineUpdate | null>(null);
  const moveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initDoneRef = useRef(false);
  const analysisStartRef = useRef<number>(0);
  const [engineEvalCp, setEngineEvalCp] = useState<number | null>(null);
  const [engineBestMoveUci, setEngineBestMoveUci] = useState<string | null>(null);

  modeRef.current = state.continuePlaying;

  // Persistent subscriber callback — stores latest update and triggers move when ready
  const onEngineUpdate = useCallback((update: EngineUpdate) => {
    latestUpdateRef.current = update;
    const topLine = update.lines[0];
    setEngineBestMoveUci(topLine?.pv[0] ?? null);

    // Engine lines are already white POV; keep them unchanged for eval bar updates.
    if (topLine) {
      if (topLine.scoreCp != null) {
        setEngineEvalCp(topLine.scoreCp);
      } else if (topLine.mate != null) {
        setEngineEvalCp(topLine.mate > 0 ? 10000 : -10000);
      }
    }

    if (!waitingForMoveRef.current) return;
    const bestMove = topLine?.pv[0];
    if (!bestMove) return;

    // Clear any pending fallback timer
    if (moveTimerRef.current) {
      clearTimeout(moveTimerRef.current);
      moveTimerRef.current = null;
    }

    if (update.depth >= ENGINE_DEPTH || update.searchComplete) {
      // Target depth reached (or engine finished early) — play the move
      // after ensuring minimum thinking time has elapsed.
      const elapsed = performance.now() - analysisStartRef.current;
      const delay = Math.max(0, MIN_THINK_MS - elapsed);
      waitingForMoveRef.current = false;
      if (delay <= 0) {
        playEngineMove(bestMove);
      } else {
        moveTimerRef.current = setTimeout(() => {
          moveTimerRef.current = null;
          playEngineMove(bestMove);
        }, delay);
      }
    } else {
      // Not at target depth yet — set fallback timer (engine might stop early)
      moveTimerRef.current = setTimeout(() => {
        moveTimerRef.current = null;
        if (waitingForMoveRef.current) {
          waitingForMoveRef.current = false;
          playEngineMove(bestMove);
        }
      }, 2000);
    }
  }, []);

  const playEngineMove = useCallback((uci: string) => {
    if (modeRef.current !== 'vs-engine') return;
    const game = gameRef.current;
    if (!game) return;

    const from = uci.slice(0, 2);
    const to = uci.slice(2, 4);
    const promotion = uci.length > 4 ? uci[4] : undefined;

    const move = game.move({ from, to, promotion });
    if (!move) return;

    dispatch({ type: 'SET_FEN', fen: game.fen() });
    dispatch({ type: 'PUSH_MOVE', san: move.san });

    // Analyze the new position for a fresh eval update.
    const engine = engineRef.current;
    if (engine && initDoneRef.current && !game.game_over()) {
      waitingForMoveRef.current = false;
      engine.analyze(game.fen());
    }
  }, [dispatch, gameRef]);

  const triggerEngineAnalysis = useCallback(() => {
    const engine = engineRef.current;
    const game = gameRef.current;
    if (!engine || !game || !initDoneRef.current) return;
    if (game.game_over()) return;

    waitingForMoveRef.current = true;
    latestUpdateRef.current = null;
    analysisStartRef.current = performance.now();
    engine.analyze(game.fen());
  }, [gameRef]);

  // Eval-only analysis — updates the eval bar without triggering a move
  const triggerEvalAnalysis = useCallback(() => {
    const engine = engineRef.current;
    const game = gameRef.current;
    if (!engine || !game || !initDoneRef.current) return;
    if (game.game_over()) return;

    waitingForMoveRef.current = false;
    engine.analyze(game.fen());
  }, [gameRef]);

  // Engine lifecycle — creation and cleanup managed inside the effect so that
  // the previous engine is always disposed BEFORE a new one is created.
  useEffect(() => {
    if (state.continuePlaying === 'off') {
      // Mode went off — clean up any leftover state
      if (engineRef.current) {
        engineRef.current.dispose();
        engineRef.current = null;
      }
      initDoneRef.current = false;
      waitingForMoveRef.current = false;
      latestUpdateRef.current = null;
      setEngineEvalCp(null);
      setEngineBestMoveUci(null);
      if (moveTimerRef.current) {
        clearTimeout(moveTimerRef.current);
        moveTimerRef.current = null;
      }
      return;
    }

    setEngineBestMoveUci(null);

    if (state.continuePlaying === 'vs-engine') {
      // Create and initialize the engine inside the effect (after previous cleanup ran)
      const worker = spawnStockfishWorker();
      const engine = new StockfishEngine(worker);
      engineRef.current = engine;
      engine.setMaxDepth(ENGINE_DEPTH);
      engine.subscribe(onEngineUpdate);

      let cancelled = false;
      void engine.init().then(() => {
        if (cancelled) return;
        initDoneRef.current = true;
        // If the opponent should move first, trigger engine analysis
        const game = gameRef.current;
        if (!game) return;
        if (game.game_over()) return;
        const turnColor = game.fen().split(' ')[1] === 'w' ? 'white' : 'black';
        const puzzleColor = state.puzzle?.player_color;
        if (puzzleColor && turnColor !== puzzleColor) {
          triggerEngineAnalysis();
        }
      });

      return () => {
        cancelled = true;
        engine.dispose();
        engineRef.current = null;
        initDoneRef.current = false;
        waitingForMoveRef.current = false;
        if (moveTimerRef.current) {
          clearTimeout(moveTimerRef.current);
          moveTimerRef.current = null;
        }
      };
    }

    // vs-self mode — spawn engine for eval-only analysis
    const worker = spawnStockfishWorker();
    const engine = new StockfishEngine(worker);
    engineRef.current = engine;
    engine.setMaxDepth(ENGINE_DEPTH);
    engine.subscribe(onEngineUpdate);

    let cancelled = false;
    void engine.init().then(() => {
      if (cancelled) return;
      initDoneRef.current = true;
      // Analyze current position for initial eval
      const game = gameRef.current;
      if (!game || game.game_over()) return;
      waitingForMoveRef.current = false;
      engine.analyze(game.fen());
    });

    return () => {
      cancelled = true;
      engine.dispose();
      engineRef.current = null;
      initDoneRef.current = false;
      if (moveTimerRef.current) {
        clearTimeout(moveTimerRef.current);
        moveTimerRef.current = null;
      }
    };
  }, [state.continuePlaying, state.puzzle?.player_color, onEngineUpdate, gameRef, triggerEngineAnalysis]);

  const startContinuePlay = useCallback((mode: ContinuePlayMode) => {
    if (mode === 'off') return;
    dispatch({ type: 'SET_CONTINUE_PLAYING', mode });
  }, [dispatch]);

  const stopContinuePlay = useCallback(() => {
    waitingForMoveRef.current = false;
    if (moveTimerRef.current) {
      clearTimeout(moveTimerRef.current);
      moveTimerRef.current = null;
    }
    setEngineEvalCp(null);
    setEngineBestMoveUci(null);
    dispatch({ type: 'SET_CONTINUE_PLAYING', mode: 'off' });
  }, [dispatch]);

  // Called after the user makes a move during continue-play
  const handleContinueMove = useCallback(() => {
    if (modeRef.current === 'vs-engine') {
      triggerEngineAnalysis();
    } else if (modeRef.current === 'vs-self') {
      triggerEvalAnalysis();
    }
  }, [triggerEngineAnalysis, triggerEvalAnalysis]);

  // Re-evaluate position (for undo/redo navigation without triggering engine moves)
  const refreshEval = useCallback(() => {
    if (modeRef.current === 'off') return;
    triggerEvalAnalysis();
  }, [triggerEvalAnalysis]);

  return {
    startContinuePlay,
    stopContinuePlay,
    handleContinueMove,
    refreshEval,
    engineEvalCp,
    engineBestMoveUci,
  };
}
