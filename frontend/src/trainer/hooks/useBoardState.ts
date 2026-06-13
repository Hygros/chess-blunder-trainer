import { useMemo, useContext } from 'preact/hooks';
import { TrainerContext } from '../context';
import {
  buildBlunderHighlight, buildBestMoveHighlight, buildUserMoveHighlight,
  buildTacticalHighlights,
} from '../highlights';
import type { HighlightMap } from '../../shared/highlights';
import { mergeHighlights } from '../../shared/highlights';
import { buildThreatHighlights } from '../../shared/threats';

export interface Arrow {
  from: string;
  to: string;
  color: string;
}

interface BoardStateResult {
  highlights: HighlightMap;
  arrows: Arrow[];
}

export function useBoardState(
  game: ChessInstance | null,
  showArrows: boolean,
  showBestArrow: boolean,
  showEngineBestArrow: boolean,
  showBlunderArrow: boolean,
  showThreats: boolean,
  showTactics: boolean,
  userMoveUci: string | null,
  engineBestMoveUci: string | null,
): BoardStateResult {
  const { state } = useContext(TrainerContext);
  const { puzzle, bestRevealed, fen, continuePlaying } = state;

  const highlights = useMemo((): HighlightMap => {
    const maps: HighlightMap[] = [buildBlunderHighlight(puzzle)];

    if (bestRevealed) {
      maps.push(buildBestMoveHighlight(puzzle));
    }

    if (showThreats && game) {
      maps.push(buildThreatHighlights(game, true));
    }

    if (showTactics && bestRevealed) {
      maps.push(buildTacticalHighlights(puzzle, game, bestRevealed, showTactics));
    }

    if (userMoveUci) {
      maps.push(buildUserMoveHighlight(userMoveUci));
    }

    return mergeHighlights(...maps);
  }, [puzzle, bestRevealed, fen, game, showThreats, showTactics, userMoveUci]);

  const arrows = useMemo((): Arrow[] => {
    if (!showArrows || !puzzle) return [];
    const result: Arrow[] = [];
    const isVsEngine = continuePlaying === 'vs-engine';

    if (!isVsEngine) {
      if (showBlunderArrow && puzzle.blunder_uci && puzzle.blunder_uci.length >= 4) {
        result.push({
          from: puzzle.blunder_uci.slice(0, 2),
          to: puzzle.blunder_uci.slice(2, 4),
          color: 'red',
        });
      }

      if (showBestArrow && bestRevealed && puzzle.best_move_uci && puzzle.best_move_uci.length >= 4) {
        result.push({
          from: puzzle.best_move_uci.slice(0, 2),
          to: puzzle.best_move_uci.slice(2, 4),
          color: 'green',
        });
      }
    }

    if (isVsEngine && showEngineBestArrow && engineBestMoveUci && engineBestMoveUci.length >= 4) {
      const sideToMove = fen.split(' ')[1] === 'b' ? 'black' : 'white';
      if (sideToMove === puzzle.player_color) {
        result.push({
          from: engineBestMoveUci.slice(0, 2),
          to: engineBestMoveUci.slice(2, 4),
          color: 'green',
        });
      }
    }

    return result;
  }, [
    showArrows,
    showBestArrow,
    showEngineBestArrow,
    showBlunderArrow,
    puzzle,
    bestRevealed,
    continuePlaying,
    fen,
    engineBestMoveUci,
  ]);

  return { highlights, arrows };
}
