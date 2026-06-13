import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/preact';
import { TrainerContext, initialState, type ContinuePlayMode, type TrainerState } from '../../src/trainer/context';
import { useBoardState } from '../../src/trainer/hooks/useBoardState';
import type { PuzzleData } from '../../src/types/api';

interface ProbeProps {
  showArrows: boolean;
  showBestArrow: boolean;
  showEngineBestArrow: boolean;
  showBlunderArrow: boolean;
  engineBestMoveUci: string | null;
}

function BoardStateProbe({
  showArrows,
  showBestArrow,
  showEngineBestArrow,
  showBlunderArrow,
  engineBestMoveUci,
}: ProbeProps): preact.JSX.Element {
  const { arrows } = useBoardState(
    null,
    showArrows,
    showBestArrow,
    showEngineBestArrow,
    showBlunderArrow,
    false,
    false,
    null,
    engineBestMoveUci,
  );

  return <div data-testid="arrows">{JSON.stringify(arrows)}</div>;
}

function makePuzzle(overrides: Partial<PuzzleData> = {}): PuzzleData {
  return {
    game_id: 'test-game',
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    ply: 1,
    blunder_uci: 'e2e4',
    blunder_san: 'e4',
    best_move_uci: 'd2d4',
    best_move_san: 'd4',
    best_line: ['d4'],
    player_color: 'white',
    eval_before: 0,
    eval_after: 0,
    eval_before_display: '0.0',
    eval_after_display: '0.0',
    cp_loss: 0,
    game_phase: 'opening',
    tactical_pattern: null,
    tactical_reason: null,
    tactical_squares: [],
    explanation_blunder: null,
    explanation_best: null,
    game_url: null,
    difficulty: 'easy',
    pre_move_uci: null,
    pre_move_fen: null,
    best_move_eval: null,
    refutation_line: null,
    refutation_line_san: null,
    refutation_eval: null,
    explanation_consequence: null,
    explanation_refutation: null,
    explanation_comparison: null,
    explanation_llm: null,
    ...overrides,
  };
}

function renderArrows(opts?: {
  continuePlaying?: ContinuePlayMode;
  fen?: string;
  bestRevealed?: boolean;
  showArrows?: boolean;
  showBestArrow?: boolean;
  showEngineBestArrow?: boolean;
  showBlunderArrow?: boolean;
  engineBestMoveUci?: string | null;
}): Array<{ from: string; to: string; color: string }> {
  const {
    continuePlaying = 'off',
    fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    bestRevealed = true,
    showArrows = true,
    showBestArrow = true,
    showEngineBestArrow = true,
    showBlunderArrow = true,
    engineBestMoveUci = null,
  } = opts ?? {};

  const state: TrainerState = {
    ...initialState,
    puzzle: makePuzzle(),
    continuePlaying,
    fen,
    bestRevealed,
  };

  render(
    <TrainerContext.Provider value={{ state, dispatch: (_action) => {} }}>
      <BoardStateProbe
        showArrows={showArrows}
        showBestArrow={showBestArrow}
        showEngineBestArrow={showEngineBestArrow}
        showBlunderArrow={showBlunderArrow}
        engineBestMoveUci={engineBestMoveUci}
      />
    </TrainerContext.Provider>,
  );

  const raw = screen.getByTestId('arrows').textContent ?? '[]';
  return JSON.parse(raw) as Array<{ from: string; to: string; color: string }>;
}

describe('useBoardState arrows', () => {
  it('shows only engine best-move arrow in vs-engine when user is to move', () => {
    const arrows = renderArrows({
      continuePlaying: 'vs-engine',
      fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      engineBestMoveUci: 'g1f3',
      showEngineBestArrow: true,
    });

    expect(arrows).toEqual([{ from: 'g1', to: 'f3', color: 'green' }]);
  });

  it('hides engine best-move arrow when player is not to move', () => {
    const arrows = renderArrows({
      continuePlaying: 'vs-engine',
      fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1',
      engineBestMoveUci: 'g1f3',
      showEngineBestArrow: true,
    });

    expect(arrows).toEqual([]);
  });

  it('shows puzzle arrows outside vs-engine and ignores engine hint arrow there', () => {
    const arrows = renderArrows({
      continuePlaying: 'off',
      engineBestMoveUci: 'g1f3',
      showEngineBestArrow: true,
      showBestArrow: true,
      showBlunderArrow: true,
      bestRevealed: true,
    });

    expect(arrows).toEqual([
      { from: 'e2', to: 'e4', color: 'red' },
      { from: 'd2', to: 'd4', color: 'green' },
    ]);
  });
});
