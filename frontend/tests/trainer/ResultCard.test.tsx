import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/preact';
import { ResultCard } from '../../src/trainer/components/ResultCard';

const puzzle = {
  game_id: 'abc', fen: 'startpos', ply: 10,
  blunder_uci: 'e2e4', blunder_san: 'e4',
  best_move_uci: 'd2d4', best_move_san: 'd4',
  best_line: ['d4', 'Nf6', 'c4'], player_color: 'white' as const,
  eval_before: 50, eval_after: -200,
  eval_before_display: '+0.5', eval_after_display: '-2.0',
  cp_loss: 250, game_phase: 'middlegame',
  tactical_pattern: 'fork', tactical_reason: 'Knight forks king and rook',
  tactical_squares: ['e5'], explanation_blunder: 'Loses material',
  explanation_best: 'Maintains advantage',
  explanation_refutation: 'Opponent can punish with ...Nf6',
  refutation_line_san: ['Nf6', 'Nc3'],
  game_url: null,
  difficulty: 'medium', pre_move_uci: null, pre_move_fen: null,
  best_move_eval: 60,
};

describe('ResultCard', () => {
  const defaults = {
    visible: true,
    feedbackType: 'correct' as const,
    feedbackTitle: 'Excellent!',
    feedbackDetail: 'You found the best move',
    puzzle,
    bestRevealed: true,
    moveHistory: [] as string[],
    lineViewIndex: 0,
    activeLineType: null,
    onPlayBest: vi.fn(),
    onNavigateLine: vi.fn(),
    onNext: vi.fn(),
    onClose: vi.fn(),
    continuePlaying: 'off' as const,
    onStartContinuePlay: vi.fn(),
    onStopContinuePlay: vi.fn(),
    onContinueUndo: vi.fn(),
    onContinueRedo: vi.fn(),
    canContinueUndo: false,
    canContinueRedo: false,
  };

  it('renders when visible', () => {
    render(<ResultCard {...defaults} />);
    expect(screen.getByText('Excellent!')).not.toBeNull();
    expect(screen.getByText('You found the best move')).not.toBeNull();
  });

  it('does not render when not visible', () => {
    const { container } = render(<ResultCard {...defaults} visible={false} />);
    expect(container.querySelector('.board-result-card')).toBeNull();
  });

  it('shows best move when revealed', () => {
    render(<ResultCard {...defaults} />);
    expect(screen.getByText('d4')).not.toBeNull();
    expect(screen.getAllByText('Nf6').length).toBeGreaterThan(0);
    expect(screen.getByText('c4')).not.toBeNull();
  });

  it('shows tactical details', () => {
    render(<ResultCard {...defaults} />);
    expect(screen.getByText('fork')).not.toBeNull();
    const tacticalDetails = document.querySelector('#tacticalDetails') as HTMLDetailsElement;
    expect(tacticalDetails.open).toBe(true);
  });

  it('shows opponent refutation label only once', () => {
    render(<ResultCard {...defaults} />);
    expect(screen.getAllByText('trainer.explanation.refutation', { exact: false })).toHaveLength(1);
  });

  it('does not mark a refutation move as active at base position', () => {
    render(<ResultCard {...defaults} activeLineType="refutation" lineViewIndex={0} />);
    expect(document.querySelector('.line-move-span.refutation.active')).toBeNull();
  });

  it('marks blunder move active at first refutation step', () => {
    render(<ResultCard {...defaults} activeLineType="refutation" lineViewIndex={1} />);
    const refutationMoves = document.querySelectorAll('.line-move-span.refutation');
    const firstRefutationMove = refutationMoves.item(0);
    expect(firstRefutationMove.classList.contains('active')).toBe(true);
  });

  it('calls onNext on next button click', () => {
    render(<ResultCard {...defaults} />);
    const nextBtn = screen.getByText('trainer.shortcuts.next', { exact: false });
    fireEvent.click(nextBtn);
    expect(defaults.onNext).toHaveBeenCalled();
  });

  it('shows play line button for correct feedback', () => {
    render(<ResultCard {...defaults} feedbackType="correct" />);
    expect(screen.getByText('trainer.button.play_line', { exact: false })).not.toBeNull();
  });

  it('calls onPlayBest when the play line button is clicked', () => {
    const onPlayBest = vi.fn();
    render(<ResultCard {...defaults} feedbackType="correct" onPlayBest={onPlayBest} />);
    fireEvent.click(screen.getByText('trainer.button.play_line', { exact: false }));
    expect(onPlayBest).toHaveBeenCalled();
  });

  it('shows play best button for non-correct feedback', () => {
    render(<ResultCard {...defaults} feedbackType="blunder" />);
    expect(screen.getByRole('button', { name: /trainer\.button\.play_best/ })).not.toBeNull();
  });
});
