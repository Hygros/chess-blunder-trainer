import { useReducer, useContext, useState, useCallback, useEffect, useRef, useMemo } from 'preact/hooks';
import { TrainerContext, trainerReducer, initialState } from './context';
import { useWebSocket } from '../hooks/useWebSocket';
import { useFeature } from '../hooks/useFeature';
import { useFilters, type FiltersAPI } from './hooks/useFilters';
import { usePuzzle } from './hooks/usePuzzle';
import { useBoardState } from './hooks/useBoardState';
import { useBoardSettings } from './hooks/useBoardSettings';
import { useLinePlayer } from './hooks/useLinePlayer';
import { useContinuePlay } from './hooks/useContinuePlay';
import { useKeyboard } from './hooks/useKeyboard';
import { playerPovToWhitePov } from '../shared/eval-bar';
import { EvalBar } from './components/EvalBar';
import { Board } from './components/Board';
import { VimInput } from './components/VimInput';
import { ContextTags } from './components/ContextTags';
import { BoardPrompt } from './components/BoardPrompt';
import { ResultCard } from './components/ResultCard';
import { FiltersPanel } from './components/FiltersPanel';
import { MoveActions } from './components/MoveActions';
import { PuzzleTools } from './components/PuzzleTools';
import { ShortcutsOverlay } from './components/ShortcutsOverlay';

export function TrainerApp(): preact.JSX.Element {
  const [state, dispatch] = useReducer(trainerReducer, initialState);
  const contextValue = useMemo(() => ({ state, dispatch }), [state, dispatch]);

  return (
    <TrainerContext.Provider value={contextValue}>
      <TrainerCore />
    </TrainerContext.Provider>
  );
}

function TrainerCore(): preact.JSX.Element {
  const { state, dispatch } = useContext(TrainerContext);
  const [submitting, setSubmitting] = useState(false);
  const [vimInputVisible, setVimInputVisible] = useState(false);
  const [userMoveUci, setUserMoveUci] = useState<string | null>(null);
  const [feedbackTitle, setFeedbackTitle] = useState('');
  const [feedbackDetail, setFeedbackDetail] = useState('');
  const [redoStack, setRedoStack] = useState<string[]>([]);
  const gameRef = useRef<ChessInstance | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastPuzzleIdRef = useRef<string | null>(null);

  // Board drag state — uses native events for reliable pointer capture
  const BOARD_OFFSET_KEY = 'blunder-tutor-board-offset';
  const boardAreaRef = useRef<HTMLDivElement>(null);
  const boardDragHandleRef = useRef<HTMLDivElement>(null);
  const boardDragging = useRef(false);
  const boardDragStart = useRef({ x: 0, y: 0, tx: 0, ty: 0 });

  // The board area is only rendered when puzzle is loaded (no emptyState/error).
  // Effects with [] deps would miss the refs because the first render is a loading shell.
  // Use this flag so effects re-run once the board DOM is actually mounted.
  const boardVisible = !state.emptyState && !state.error && !!state.puzzle;

  // Restore persisted board offset when board mounts
  useEffect(() => {
    if (!boardVisible) return;
    const area = boardAreaRef.current;
    if (!area) return;
    try {
      const saved = localStorage.getItem(BOARD_OFFSET_KEY);
      if (saved) {
        const { tx, ty } = JSON.parse(saved) as { tx: number; ty: number };
        if (tx !== 0 || ty !== 0) {
          area.style.transform = `translate(${String(tx)}px, ${String(ty)}px)`;
          // Invalidate Chessground bounds cache so piece interactions use correct position
          document.dispatchEvent(new Event('scroll'));
        }
      }
    } catch { /* ignore corrupt storage */ }
  }, [boardVisible]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!boardVisible) return;
    const handle = boardDragHandleRef.current;
    const area = boardAreaRef.current;
    if (!handle || !area) return;

    const applyOffset = (tx: number, ty: number) => {
      area.style.transform = `translate(${String(tx)}px, ${String(ty)}px)`;
      // Invalidate Chessground bounds cache so piece interactions use correct position
      document.dispatchEvent(new Event('scroll'));
    };

    const persistOffset = (tx: number, ty: number) => {
      try { localStorage.setItem(BOARD_OFFSET_KEY, JSON.stringify({ tx, ty })); } catch { /* quota */ }
    };

    const onDown = (e: PointerEvent) => {
      e.preventDefault();
      boardDragging.current = true;
      area.classList.add('dragging');
      const style = getComputedStyle(area);
      const tf = style.transform;
      const matrix = tf && tf !== 'none' ? new DOMMatrix(tf) : new DOMMatrix();
      boardDragStart.current = {
        x: e.clientX, y: e.clientY,
        tx: matrix.m41, ty: matrix.m42,
      };
      handle.setPointerCapture(e.pointerId);
    };

    const onMove = (e: PointerEvent) => {
      if (!boardDragging.current) return;
      const s = boardDragStart.current;
      const tx = s.tx + (e.clientX - s.x);
      const ty = s.ty + (e.clientY - s.y);
      applyOffset(tx, ty);
    };

    const onUp = () => {
      if (!boardDragging.current) return;
      boardDragging.current = false;
      area.classList.remove('dragging');
      // Persist final position
      const style = getComputedStyle(area);
      const tf = style.transform;
      const matrix = tf && tf !== 'none' ? new DOMMatrix(tf) : new DOMMatrix();
      persistOffset(matrix.m41, matrix.m42);
    };

    // Double-click handle to reset board position
    const onDblClick = () => {
      area.style.transform = '';
      persistOffset(0, 0);
      document.dispatchEvent(new Event('scroll'));
    };

    handle.addEventListener('pointerdown', onDown);
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('lostpointercapture', onUp);
    handle.addEventListener('dblclick', onDblClick);

    return () => {
      handle.removeEventListener('pointerdown', onDown);
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('lostpointercapture', onUp);
      handle.removeEventListener('dblclick', onDblClick);
    };
  }, [boardVisible]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasPreMove = useFeature('trainer.pre_move');

  // Sync game from puzzle — synchronous, not in effect
  if (state.puzzle && state.puzzle.game_id !== lastPuzzleIdRef.current) {
    lastPuzzleIdRef.current = state.puzzle.game_id;
    gameRef.current = new Chess(state.puzzle.fen);
  } else if (!state.puzzle && lastPuzzleIdRef.current) {
    lastPuzzleIdRef.current = null;
    gameRef.current = null;
  }

  const puzzleApi = usePuzzle();
  useBoardSettings();

  // Filters — onFilterChange triggers a new puzzle load
  const filtersRef = useRef<FiltersAPI | null>(null);
  const filtersApi = useFilters(useCallback(() => {
    setUserMoveUci(null);
    const params = filtersRef.current?.getFilterParams();
    void puzzleApi.loadPuzzle(params);
  }, [puzzleApi]));
  filtersRef.current = filtersApi;

  // Line player
  const { playBestMove, navigateLine, navigateLineTyped } = useLinePlayer(gameRef);

  // Continue-play (vs engine / vs self)
  const { startContinuePlay, stopContinuePlay, handleContinueMove, refreshEval, engineEvalCp } = useContinuePlay(gameRef);

  // WebSocket for stats updates
  const ws = useWebSocket(['stats.updated']);
  useEffect(() => {
    const unsub = ws.on('stats.updated', () => {
      if (typeof htmx !== 'undefined') {
        htmx.trigger(document.querySelector('#statsContent') ?? document.body, 'statsUpdate');
      }
    });
    return unsub;
  }, [ws]);

  // Board state (highlights + arrows)
  const { highlights, arrows } = useBoardState(
    gameRef.current,
    filtersApi.state.showArrows,
    filtersApi.state.showThreats,
    filtersApi.state.showTactics,
    userMoveUci,
  );

  // Pre-move animation config
  const animateFrom = useMemo(() => {
    const puzzle = state.puzzle;
    if (!puzzle || !hasPreMove || !puzzle.pre_move_uci || !puzzle.pre_move_fen) return null;
    return {
      fen: puzzle.pre_move_fen,
      from: puzzle.pre_move_uci.slice(0, 2),
      to: puzzle.pre_move_uci.slice(2, 4),
      onComplete: () => { dispatch({ type: 'SET_ANIMATING', animating: false }); },
    };
  }, [state.puzzle, hasPreMove, dispatch]);

  // Submit move
  const handleSubmit = useCallback(async () => {
    const game = gameRef.current;
    if (!game) return;
    const history = game.history({ verbose: true });
    const lastMove = history[history.length - 1];
    if (!lastMove) return;

    const uci = lastMove.from + lastMove.to + (lastMove.promotion || '');
    setSubmitting(true);
    const data = await puzzleApi.submitMove(uci);
    setSubmitting(false);

    if (!data) {
      setFeedbackTitle(t('trainer.feedback.error'));
      setFeedbackDetail(t('trainer.feedback.submit_failed'));
      dispatch({ type: 'SET_RESULT_VISIBLE', visible: true });
      return;
    }

    if (data.is_best) {
      setFeedbackTitle(t('trainer.feedback.excellent'));
      setFeedbackDetail(t('trainer.feedback.found_best'));
      dispatch({ type: 'SET_FEEDBACK', feedbackType: 'correct' });
    } else if (data.is_blunder) {
      setFeedbackTitle(t('trainer.feedback.same_blunder'));
      setFeedbackDetail(t('trainer.feedback.same_blunder_detail', { userMove: data.user_san }));
      dispatch({ type: 'SET_FEEDBACK', feedbackType: 'blunder' });
    } else {
      const evalDiff = Math.abs(data.user_eval - (state.puzzle?.eval_before ?? 0));
      if (evalDiff < 50) {
        setFeedbackTitle(t('trainer.feedback.good_move'));
        setFeedbackDetail(t('trainer.feedback.good_move_detail', { userMove: data.user_san }));
        dispatch({ type: 'SET_FEEDBACK', feedbackType: 'good' });
      } else {
        setFeedbackTitle(t('trainer.feedback.not_quite'));
        setFeedbackDetail(t('trainer.feedback.not_quite_detail', { userMove: data.user_san, userEval: data.user_eval_display }));
        dispatch({ type: 'SET_FEEDBACK', feedbackType: 'not-quite' });
      }
      setUserMoveUci(data.user_uci);
    }

    dispatch({ type: 'REVEAL_BEST' });
    dispatch({ type: 'SET_RESULT_VISIBLE', visible: true });

    if (typeof htmx !== 'undefined') {
      htmx.trigger(document.body, 'statsUpdate');
    }
  }, [puzzleApi, state.puzzle, dispatch]);

  // Board move handler
  const handleSubmitRef = useRef(handleSubmit);
  handleSubmitRef.current = handleSubmit;

  const onBoardMove = useCallback((orig: string, dest: string, move: { san: string; from: string; to: string; promotion?: string }) => {
    if (state.animating) return;
    const game = gameRef.current;
    if (!game) return;

    dispatch({ type: 'SET_FEN', fen: game.fen() });

    if (state.bestRevealed) {
      dispatch({ type: 'PUSH_MOVE', san: move.san });
      setRedoStack([]);
      handleContinueMove();
    } else if (!state.submitted) {
      const puzzle = state.puzzle;
      const uci = move.from + move.to + (move.promotion || '');
      if (puzzle && uci === puzzle.best_move_uci) {
        setTimeout(() => { void handleSubmitRef.current(); }, 150);
      }
    }
  }, [state.animating, state.bestRevealed, state.submitted, state.puzzle, dispatch, handleContinueMove]);

  // Reveal best move
  const handleReveal = useCallback(() => {
    if (state.bestRevealed) {
      dispatch({ type: 'SET_RESULT_VISIBLE', visible: !state.resultVisible });
    } else {
      setFeedbackTitle(t('trainer.feedback.best_revealed'));
      setFeedbackDetail(t('trainer.feedback.best_revealed_detail'));
      dispatch({ type: 'REVEAL_BEST' });
      dispatch({ type: 'SET_RESULT_VISIBLE', visible: true });
    }
  }, [state.bestRevealed, state.resultVisible, dispatch]);

  // Reset position
  const handleReset = useCallback(() => {
    if (state.animating) return;
    const puzzle = state.puzzle;
    if (!puzzle) return;
    if (state.continuePlaying !== 'off') {
      stopContinuePlay();
    }
    gameRef.current = new Chess(puzzle.fen);
    dispatch({ type: 'SET_FEN', fen: puzzle.fen });
    dispatch({ type: 'CLEAR_LINE_NAVIGATION' });
    dispatch({ type: 'CLEAR_MOVES' });
    if (!state.bestRevealed) {
      dispatch({ type: 'SET_RESULT_VISIBLE', visible: false });
    }
  }, [state.animating, state.puzzle, state.bestRevealed, state.continuePlaying, stopContinuePlay, dispatch]);

  // Undo
  const handleUndo = useCallback(() => {
    if (state.animating) return;
    const game = gameRef.current;
    if (!game || game.history().length === 0) return;
    if (state.continuePlaying !== 'off') {
      stopContinuePlay();
    }
    game.undo();
    dispatch({ type: 'SET_FEN', fen: game.fen() });
    dispatch({ type: 'POP_MOVE' });
  }, [state.animating, state.continuePlaying, stopContinuePlay, dispatch]);

  // Undo/Redo during continue play (does not stop engine/self mode)
  const handleContinueUndo = useCallback(() => {
    if (state.animating) return;
    const game = gameRef.current;
    if (!game || state.moveHistory.length === 0) return;
    const lastSan = state.moveHistory[state.moveHistory.length - 1];
    game.undo();
    dispatch({ type: 'SET_FEN', fen: game.fen() });
    dispatch({ type: 'POP_MOVE' });
    setRedoStack(prev => [...prev, lastSan!]);
    refreshEval();
  }, [state.animating, state.moveHistory, dispatch, refreshEval]);

  const handleContinueRedo = useCallback(() => {
    if (state.animating) return;
    const game = gameRef.current;
    if (!game || redoStack.length === 0) return;
    const san = redoStack[redoStack.length - 1];
    const move = game.move(san!);
    if (!move) return;
    dispatch({ type: 'SET_FEN', fen: game.fen() });
    dispatch({ type: 'PUSH_MOVE', san: move.san });
    setRedoStack(prev => prev.slice(0, -1));
    refreshEval();
  }, [state.animating, redoStack, dispatch, refreshEval]);

  // Flip board
  const handleFlip = useCallback(() => {
    const puzzle = state.puzzle;
    if (!puzzle) return;
    const flipped = !state.boardFlipped;
    dispatch({ type: 'SET_BOARD_FLIPPED', flipped });
    const base = puzzle.player_color === 'black' ? 'black' : 'white';
    dispatch({ type: 'SET_ORIENTATION', orientation: flipped ? (base === 'white' ? 'black' : 'white') : base });
  }, [state.puzzle, state.boardFlipped, dispatch]);

  // Lichess analysis
  const openLichess = useCallback(() => {
    const puzzle = state.puzzle;
    const game = gameRef.current;
    if (!puzzle || !game) return;
    const fen = game.fen().replace(/ /g, '_');
    const lichessArrows: string[] = [];
    if (game.fen() === puzzle.fen) {
      if (puzzle.blunder_uci && puzzle.blunder_uci.length >= 4) lichessArrows.push(`R${puzzle.blunder_uci.slice(0, 2)}${puzzle.blunder_uci.slice(2, 4)}`);
      if (puzzle.best_move_uci && puzzle.best_move_uci.length >= 4) lichessArrows.push(`G${puzzle.best_move_uci.slice(0, 2)}${puzzle.best_move_uci.slice(2, 4)}`);
    }
    const hash = lichessArrows.length > 0 ? '#' + lichessArrows.join(',') : '';
    window.open(`https://lichess.org/analysis/${fen}?color=${puzzle.player_color}${hash}`, '_blank');
  }, [state.puzzle]);

  // Next puzzle
  const handleNext = useCallback(() => {
    trackEvent('Puzzle Next');
    setUserMoveUci(null);
    void puzzleApi.loadPuzzle(filtersApi.getFilterParams());
  }, [puzzleApi, filtersApi]);

  // Has move check
  const hasMove = useMemo(() => {
    const game = gameRef.current;
    return !!game && game.history().length > 0;
  }, [state.fen]);

  // Keyboard shortcuts
  useKeyboard({
    submit: () => { void handleSubmit(); },
    next: handleNext,
    reset: handleReset,
    undo: handleUndo,
    flip: handleFlip,
    reveal: handleReveal,
    playBest: playBestMove,
    lichess: openLichess,
    vimInput: () => {
      if (!state.animating && !state.submitted && state.puzzle) {
        setVimInputVisible(true);
      }
    },
    toggleShortcuts: () => { dispatch({ type: 'TOGGLE_SHORTCUTS' }); },
    navigateLine,
    toggleArrows: () => { filtersApi.setShowArrows(!filtersApi.state.showArrows); },
    toggleThreats: () => { filtersApi.setShowThreats(!filtersApi.state.showThreats); },
    isAnimating: state.animating,
    isVimInputActive: vimInputVisible,
    isShortcutsVisible: state.shortcutsVisible,
    isResultVisible: state.resultVisible,
    hideResult: () => { dispatch({ type: 'SET_RESULT_VISIBLE', visible: false }); },
  });

  // Initial load
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const deepGameId = urlParams.get('game_id');
    const deepPly = urlParams.get('ply');
    if (deepGameId && deepPly) {
      void puzzleApi.loadSpecificPuzzle(deepGameId, deepPly);
    } else {
      void puzzleApi.loadPuzzle(filtersApi.getFilterParams());
    }
  }, []); // mount-only: deep-link check runs once

  // Retry for analyzing state
  useEffect(() => {
    if (state.emptyState === 'analyzing') {
      retryTimerRef.current = setTimeout(() => {
        void puzzleApi.loadPuzzle(filtersApi.getFilterParams());
      }, 5000);
      return () => {
        if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      };
    }
  }, [state.emptyState, puzzleApi, filtersApi]);

  // Empty state / error
  if (state.emptyState || state.error) {
    return (
      <div class="trainer-page">
        <div class="empty-state" id="emptyState">
          <h2>{state.error || t(`trainer.empty.${state.emptyState ?? 'default'}_title`)}</h2>
          <p>{t(`trainer.empty.${state.emptyState ?? 'default'}_message`)}</p>
          {state.emptyState === 'no_blunders_filtered' ? (
            <button onClick={filtersApi.clearAllFilters}>
              {t('trainer.empty.no_matching_action')}
            </button>
          ) : (
            <a href="/management">{t(`trainer.empty.${state.emptyState ?? 'default'}_action`)}</a>
          )}
        </div>
      </div>
    );
  }

  // Initial load — hide layout until first puzzle arrives to prevent layout shift
  if (!state.puzzle) {
    return <div class="trainer-page" />;
  }

  const interactive = !state.animating && (!state.submitted || state.bestRevealed) && !!state.puzzle;

  return (
    <div class="trainer-page">
      <div class="trainer-main" id="trainerLayout">
        <div class="trainer-board-area" ref={boardAreaRef}>
          <div
            ref={boardDragHandleRef}
            class="board-drag-handle"
            title="Drag to move board"
          >⠇</div>
          <ContextTags puzzle={state.puzzle} />
          <div class="board-eval-wrapper">
            <EvalBar
              cp={
                engineEvalCp != null
                  ? engineEvalCp
                  : playerPovToWhitePov(
                    state.puzzle.eval_before,
                    state.puzzle.player_color,
                  )
              }
            />
            <div id="boardWrapper">
              <Board
                fen={state.fen}
                orientation={state.orientation}
                interactive={interactive}
                movableColor={state.continuePlaying === 'vs-self' ? 'both' : undefined}
                coordinates={filtersApi.state.showCoordinates}
                highlights={highlights}
                arrows={arrows}
                gameRef={gameRef}
                onMove={onBoardMove}
                animateFrom={animateFrom}
                moveCount={state.moveHistory.length}
              />
              <VimInput
            visible={vimInputVisible}
            game={gameRef.current}
            interactive={interactive}
            onMove={(move) => {
              onBoardMove(move.from, move.to, move);
              setVimInputVisible(false);
            }}
            onClose={() => { setVimInputVisible(false); }}
          />
            </div>
          </div>
          <BoardPrompt
            submitted={state.submitted}
            bestRevealed={state.bestRevealed}
            submitting={submitting}
            hasPuzzle={!!state.puzzle}
          />
        </div>

        <ResultCard
          visible={state.resultVisible}
          feedbackType={state.feedbackType}
          feedbackTitle={feedbackTitle}
          feedbackDetail={feedbackDetail}
          puzzle={state.puzzle}
          bestRevealed={state.bestRevealed}
          moveHistory={state.moveHistory}
          lineViewIndex={state.lineViewIndex}
          activeLineType={state.activeLineType}
          onPlayBest={playBestMove}
          onNavigateLine={navigateLineTyped}
          onNext={handleNext}
          onClose={() => { dispatch({ type: 'SET_RESULT_VISIBLE', visible: false }); }}
          continuePlaying={state.continuePlaying}
          onStartContinuePlay={startContinuePlay}
          onStopContinuePlay={stopContinuePlay}
          onContinueUndo={handleContinueUndo}
          onContinueRedo={handleContinueRedo}
          canContinueUndo={state.moveHistory.length > 0}
          canContinueRedo={redoStack.length > 0}
        />

        <div class="trainer-panel">
          <PuzzleTools
            puzzle={state.puzzle}
            starred={state.currentStarred}
            onStarredChange={(starred: boolean) => { dispatch({ type: 'SET_STARRED', starred }); }}
          />
          <MoveActions
            hasPuzzle={!!state.puzzle}
            submitted={state.submitted}
            bestRevealed={state.bestRevealed}
            submitting={submitting}
            hasMove={hasMove}
            onSubmit={() => { void handleSubmit(); }}
            onReset={handleReset}
            onReveal={handleReveal}
            onNext={handleNext}
            onUndo={handleUndo}
            onShowShortcuts={() => { dispatch({ type: 'TOGGLE_SHORTCUTS' }); }}
          />
          <FiltersPanel filters={filtersApi} />
        </div>
      </div>

      <ShortcutsOverlay
        visible={state.shortcutsVisible}
        onClose={() => { dispatch({ type: 'TOGGLE_SHORTCUTS' }); }}
      />
    </div>
  );
}
