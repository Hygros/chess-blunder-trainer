import { useRef, useEffect } from 'preact/hooks';
import { useDrag } from '../hooks/useDrag';
import type { PuzzleData, FeedbackType, ActiveLineType, ContinuePlayMode } from '../context';

interface ResultCardProps {
  visible: boolean;
  feedbackType: FeedbackType;
  feedbackTitle: string;
  feedbackDetail: string;
  puzzle: PuzzleData | null;
  bestRevealed: boolean;
  moveHistory: string[];
  lineViewIndex: number;
  activeLineType: ActiveLineType;
  onPlayBest: () => void;
  onNavigateLine: (lineType: 'best' | 'refutation', direction: 'forward' | 'back') => void;
  onNext: () => void;
  onClose: () => void;
  continuePlaying: ContinuePlayMode;
  onStartContinuePlay: (mode: ContinuePlayMode) => void;
  onStopContinuePlay: () => void;
  onContinueUndo: () => void;
  onContinueRedo: () => void;
  canContinueUndo: boolean;
  canContinueRedo: boolean;
}

const ACCENT_MAP: Record<string, string> = {
  correct: 'accent-correct',
  blunder: 'accent-blunder',
  good: 'accent-correct',
  'not-quite': 'accent-revealed',
};

export function ResultCard({
  visible, feedbackType, feedbackTitle, feedbackDetail, puzzle,
  bestRevealed, moveHistory, lineViewIndex, activeLineType,
  onPlayBest, onNavigateLine, onNext, onClose: _onClose,
  continuePlaying, onStartContinuePlay, onStopContinuePlay,
  onContinueUndo, onContinueRedo, canContinueUndo, canContinueRedo,
}: ResultCardProps): preact.JSX.Element | null {
  const cardRef = useRef<HTMLDivElement>(null);
  const { handleRef, restorePosition } = useDrag(cardRef, visible);

  useEffect(() => {
    if (visible) {
      requestAnimationFrame(() => { restorePosition(); });
    } else if (cardRef.current) {
      cardRef.current.style.left = '';
      cardRef.current.style.top = '';
      cardRef.current.style.right = '';
      cardRef.current.style.bottom = '';
    }
  }, [visible, restorePosition]);

  if (!visible) return null;

  const accentClass = feedbackType ? ACCENT_MAP[feedbackType] ?? 'accent-revealed' : 'accent-revealed';
  const displayedRefutationLine = puzzle && puzzle.refutation_line_san && puzzle.refutation_line_san.length > 0
    ? [puzzle.blunder_san, ...puzzle.refutation_line_san]
    : null;
  const consequenceText = puzzle ? (puzzle.explanation_consequence || puzzle.explanation_blunder || '') : '';
  const comparisonText = puzzle ? (puzzle.explanation_comparison || puzzle.explanation_best || '') : '';
  const hasExplanationContent = Boolean(puzzle && (puzzle.explanation_llm || consequenceText || comparisonText));

  return (
    <div
      ref={cardRef}
      id="boardResultCard"
      class={`board-result-card visible ${accentClass} ${bestRevealed ? 'best-revealed' : ''}`}
    >
      <div class="board-result-inner">
        <div ref={handleRef} class="board-result-drag-handle" id="boardResultDragHandle">
          <div class="board-result-drag-handle-bar" />
        </div>
        <div class="board-result-header" id="boardResultHeader">
          <div class="board-result-title" id="feedbackTitle">{feedbackTitle}</div>
          <div class="board-result-detail" id="feedbackDetail">{feedbackDetail}</div>
        </div>
        <div class="board-result-body">
          {puzzle && bestRevealed && (
            <>
              <div class="board-result-line-section board-result-best-section">
                <div class="line-section-header">
                  <span class="line-section-label line-section-label--best">✅ {t('trainer.button.play_best')}</span>
                  <div class="line-nav-buttons">
                    <button
                      class="line-nav-btn"
                      onClick={() => { onNavigateLine('best', 'back'); }}
                      disabled={activeLineType === 'best' && lineViewIndex <= 0}
                      title="◀"
                    >◀</button>
                    <button
                      class="line-nav-btn"
                      onClick={() => { onNavigateLine('best', 'forward'); }}
                      disabled={activeLineType === 'best' && lineViewIndex >= puzzle.best_line.length}
                      title="▶"
                    >▶</button>
                  </div>
                </div>
                <div class="line-moves-display">
                  {puzzle.best_line.map((san, i) => (
                    <span
                      key={i}
                      class={`line-move-span${activeLineType === 'best' && lineViewIndex === i + 1 ? ' active' : ''}`}
                    >
                      {san}
                    </span>
                  ))}
                </div>
              </div>

              <div class="board-result-action">
                <button class="btn btn-success" id="tryBestBtn" onClick={onPlayBest}>
                  {feedbackType === 'correct' ? t('trainer.button.play_line') : t('trainer.button.play_best')}<kbd>P</kbd>
                </button>
              </div>

              {displayedRefutationLine && (
                <div class="board-result-line-section board-result-refutation-section">
                  <div class="line-section-header">
                    <span class="line-section-label line-section-label--refutation">♟ {t('trainer.explanation.refutation')}</span>
                    <div class="line-nav-buttons">
                      <button
                        class="line-nav-btn"
                        onClick={() => { onNavigateLine('refutation', 'back'); }}
                        disabled={activeLineType === 'refutation' && lineViewIndex <= 0}
                        title="◀"
                      >◀</button>
                      <button
                        class="line-nav-btn"
                        onClick={() => { onNavigateLine('refutation', 'forward'); }}
                        disabled={activeLineType === 'refutation' && lineViewIndex >= puzzle.refutation_line_san!.length}
                        title="▶"
                      >▶</button>
                    </div>
                  </div>
                  <div class="line-moves-display">
                    {displayedRefutationLine.map((san, i) => (
                      <span
                        key={i}
                        class={`line-move-span refutation${activeLineType === 'refutation' && lineViewIndex === i ? ' active' : ''}`}
                      >
                        {san}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {puzzle.tactical_pattern && puzzle.tactical_pattern !== 'None' && puzzle.tactical_reason && (
                <details class="board-result-details" id="tacticalDetails" open>
                  <summary>{t('trainer.details.tactical')}</summary>
                  <div class="board-result-details-body">
                    <div class="board-result-details-heading" id="tacticalInfoTitle">{puzzle.tactical_pattern}</div>
                    <div class="board-result-details-text" id="tacticalInfoReason">{puzzle.tactical_reason}</div>
                  </div>
                </details>
              )}

              {hasExplanationContent && (
                <div class="board-result-explanation" id="explanationDetails">
                  {puzzle.explanation_llm && (
                    <div class="board-result-explanation-section">
                      <div class="board-result-details-heading">🤖 {t('trainer.explanation.ai')}</div>
                      <div id="explanationLlm">{puzzle.explanation_llm}</div>
                    </div>
                  )}
                  {consequenceText && (
                    <div class="board-result-explanation-section">
                      <div class="board-result-details-heading">🔴 {t('trainer.explanation.consequence')}</div>
                      <div id="explanationConsequence">{consequenceText}</div>
                    </div>
                  )}
                  {comparisonText && (
                    <div class="board-result-explanation-section">
                      <div class="board-result-details-heading">✅ {t('trainer.explanation.comparison')}</div>
                      <div id="explanationComparison">{comparisonText}</div>
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {moveHistory.length > 0 && (
            <div class="move-history-section">
              <div class="move-history">{moveHistory.join(' ')}</div>
            </div>
          )}

          {puzzle && bestRevealed && (
            <div class="board-result-continue-section">
              {continuePlaying === 'off' ? (
                <div class="continue-play-buttons">
                  <button
                    class="btn btn-continue btn-continue--engine"
                    onClick={() => { onStartContinuePlay('vs-engine'); }}
                  >
                    ▶ {t('trainer.continue.vs_engine')}
                  </button>
                  <button
                    class="btn btn-continue btn-continue--self"
                    onClick={() => { onStartContinuePlay('vs-self'); }}
                  >
                    ▶ {t('trainer.continue.vs_self')}
                  </button>
                </div>
              ) : (
                <div class="continue-play-active">
                  <span class="continue-play-label">
                    {continuePlaying === 'vs-engine'
                      ? t('trainer.continue.playing_engine')
                      : t('trainer.continue.playing_self')}
                  </span>
                  <div class="continue-play-nav">
                    <button
                      class="line-nav-btn"
                      onClick={onContinueUndo}
                      disabled={!canContinueUndo}
                      title={t('trainer.shortcuts.undo')}
                    >◀</button>
                    <button
                      class="line-nav-btn"
                      onClick={onContinueRedo}
                      disabled={!canContinueRedo}
                      title="Redo"
                    >▶</button>
                  </div>
                  <button
                    class="btn btn-sm btn-continue--stop"
                    onClick={onStopContinuePlay}
                  >
                    ■ {t('trainer.continue.stop')}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
        <div class="board-result-next">
          <button class="btn btn-primary board-result-next-btn" id="overlayNextBtn" onClick={onNext}>
            {t('trainer.shortcuts.next')}<kbd>N</kbd>
          </button>
        </div>
      </div>
    </div>
  );
}
