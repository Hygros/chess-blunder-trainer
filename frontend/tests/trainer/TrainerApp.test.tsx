import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, screen, fireEvent } from '@testing-library/preact';
import { STORAGE_KEYS } from '../../src/shared/storage-keys';

const { mockChessground } = vi.hoisted(() => {
  const mockCg = {
    set: vi.fn(),
    setAutoShapes: vi.fn(),
    destroy: vi.fn(),
  };
  return { mockCg, mockChessground: vi.fn(function() { return mockCg; }) };
});

vi.mock('@vendor/chessground', () => ({
  Chessground: mockChessground,
}));

vi.mock('../../src/shared/api', () => ({
  client: {
    trainer: {
      getPuzzle: vi.fn().mockResolvedValue({
        game_id: 'test123', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        ply: 10, blunder_uci: 'e2e4', blunder_san: 'e4',
        best_move_uci: 'd2d4', best_move_san: 'd4', best_line: ['d4'],
        player_color: 'white', eval_before: 50, eval_after: -200,
        eval_before_display: '+0.5', eval_after_display: '-2.0', cp_loss: 250,
        game_phase: 'middlegame', tactical_pattern: null, tactical_reason: null,
        tactical_squares: [], explanation_blunder: null, explanation_best: null,
        game_url: null, difficulty: 'medium', pre_move_uci: null, pre_move_fen: null,
        best_move_eval: 60,
      }),
      getSpecificPuzzle: vi.fn(),
      submitMove: vi.fn(),
    },
    settings: { getBoard: vi.fn().mockResolvedValue({ piece_set: 'cburnett', board_light: '#f0d9b5', board_dark: '#b58863' }) },
    jobs: { list: vi.fn().mockResolvedValue([]) },
    starred: { isStarred: vi.fn().mockResolvedValue({ starred: false }) },
    debug: { gameInfo: vi.fn() },
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(m: string, s: number) { super(m); this.status = s; }
  },
}));

vi.mock('../../src/hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(() => ({ on: vi.fn(() => vi.fn()) })),
}));

import { TrainerApp } from '../../src/trainer/TrainerApp';

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  window.__features = {};
  (globalThis as Record<string, unknown>).Chess = vi.fn(function() {
    return {
      fen: vi.fn(() => 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'),
      turn: vi.fn(() => 'w'),
      moves: vi.fn(() => []),
      move: vi.fn(),
      undo: vi.fn(),
      history: vi.fn(() => []),
      game_over: vi.fn(() => false),
      in_check: vi.fn(() => false),
      load: vi.fn(() => true),
      board: vi.fn(() => []),
      get: vi.fn(),
      put: vi.fn(),
      remove: vi.fn(),
      pgn: vi.fn(() => ''),
      load_pgn: vi.fn(() => true),
    };
  });
});

describe('TrainerApp', () => {
  it('renders the trainer layout', async () => {
    render(<TrainerApp />);
    await waitFor(() => {
      expect(document.querySelector('.trainer-page')).not.toBeNull();
    });
  });

  it('loads a puzzle on mount', async () => {
    const { client } = await import('../../src/shared/api');
    render(<TrainerApp />);
    await waitFor(() => {
      expect(client.trainer.getPuzzle).toHaveBeenCalled();
    });
  });

  it('shows no-matching state with clear action when filters are active', async () => {
    const { client, ApiError } = await import('../../src/shared/api');
    vi.mocked(client.trainer.getPuzzle).mockRejectedValueOnce(new ApiError('No blunders found.', 400));
    localStorage.setItem('blunder-tutor-color-filter', 'white');

    render(<TrainerApp />);

    await waitFor(() => {
      expect(screen.getByText('trainer.empty.no_matching_title')).toBeTruthy();
      expect(screen.getByText('trainer.empty.no_matching_action')).toBeTruthy();
      expect(document.querySelector('.filters-panel')).not.toBeNull();
    });
  });

  it('shows no-blunders state when no filters are active and keeps filters visible', async () => {
    const { client, ApiError } = await import('../../src/shared/api');
    vi.mocked(client.trainer.getPuzzle).mockRejectedValueOnce(new ApiError('No blunders found.', 400));

    render(<TrainerApp />);

    await waitFor(() => {
      expect(screen.getByText('trainer.empty.no_blunders_title')).toBeTruthy();
      expect(screen.getByText('trainer.empty.no_blunders_action')).toBeTruthy();
      expect(document.querySelector('.filters-panel')).not.toBeNull();
    });
  });

  it('enables blunder arrow again when switching to the next puzzle', async () => {
    const { client } = await import('../../src/shared/api');
    localStorage.setItem(STORAGE_KEYS.trainerShowBlunderArrow, 'false');

    render(<TrainerApp />);

    await waitFor(() => {
      expect(client.trainer.getPuzzle).toHaveBeenCalledTimes(1);
    });

    const nextButton = document.querySelector('#nextBtn');
    expect(nextButton).not.toBeNull();
    fireEvent.click(nextButton as HTMLElement);

    await waitFor(() => {
      expect(localStorage.getItem(STORAGE_KEYS.trainerShowBlunderArrow)).toBe('true');
    });
  });
});
