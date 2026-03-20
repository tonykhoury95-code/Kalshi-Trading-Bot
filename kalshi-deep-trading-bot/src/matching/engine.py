"""
Market matching engine.

Finds the sportsbook game that corresponds to a Kalshi market, and
determines which side (home/away) the Kalshi YES contract represents.

Matching pipeline
-----------------
1. Filter sportsbook games by sport.
2. Extract team token(s) from the Kalshi ticker.
3. For each candidate game, score:
   a. Team name match quality (exact / fuzzy).
   b. Time-window check (sportsbook start vs Kalshi close time).
4. Return the best match above the confidence threshold, or None.

The engine is stateless and has no I/O — all inputs are passed in.
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.matching.models import MatchMethod, MatchResult, YesSide
from src.matching.normalizer import TeamNormalizer
from src.sportsbook.models import SportsbookGame
from src.utils.time import hours_until


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

class MatchingEngine:
    """Link Kalshi markets to sportsbook games.

    Args:
        min_confidence: Reject matches below this score (default 0.75).
        time_window_hours: Maximum abs difference between Kalshi close time
                           and sportsbook commence time (default 6.0h).
        fuzzy_threshold: Minimum similarity for fuzzy team-name matching.
    """

    def __init__(
        self,
        min_confidence: float = 0.75,
        time_window_hours: float = 6.0,
        fuzzy_threshold: float = 0.75,
    ) -> None:
        self._min_confidence = min_confidence
        self._time_window_hours = time_window_hours
        self._fuzzy_threshold = fuzzy_threshold
        self._normalizer = TeamNormalizer()

    # -- Public API -----------------------------------------------------------

    def find_match(
        self,
        kalshi_market: KalshiMarket,
        kalshi_event: KalshiEvent,
        sportsbook_games: list[SportsbookGame],
        sport: str,
    ) -> Optional[MatchResult]:
        """Find the best matching sportsbook game for a Kalshi market.

        Args:
            kalshi_market: The Kalshi market to match.
            kalshi_event:  The parent Kalshi event.
            sportsbook_games: All games fetched for this sport from the provider.
            sport: Sport code (``"NBA"``, ``"NFL"``, etc.).

        Returns:
            A :class:`MatchResult` if a confident match is found, else ``None``.
        """
        sport_upper = sport.upper()

        # Extract team tokens from the Kalshi ticker
        tokens = self._normalizer.extract_team_tokens(kalshi_market.ticker, sport_upper)

        if not tokens:
            logger.debug(
                "Matching: no team tokens in '{}' (sport={}) — skipping",
                kalshi_market.ticker, sport_upper,
            )
            return None

        # Filter games to the same sport and compute candidate scores
        best: Optional[MatchResult] = None
        best_conf = -1.0

        for game in sportsbook_games:
            if game.sport != sport_upper:
                continue

            result = self._score_candidate(
                kalshi_market=kalshi_market,
                tokens=tokens,
                game=game,
                sport=sport_upper,
            )
            if result is None:
                continue

            if result.confidence > best_conf:
                best_conf = result.confidence
                best = result

        if best is None:
            logger.info(
                "Matching REJECTED '{}': no candidate passed time-window or team-scoring filters "
                "(sport={}, games_checked={})",
                kalshi_market.ticker,
                sport_upper,
                sum(1 for g in sportsbook_games if g.sport == sport_upper),
            )
            return None

        if best.confidence < self._min_confidence:
            logger.info(
                "Matching REJECTED '{}': best match '{}' vs '{}' conf={:.2f} < threshold={:.2f} "
                "(method={}, time_delta={:.1f}h)",
                kalshi_market.ticker,
                best.home_team,
                best.away_team,
                best.confidence,
                self._min_confidence,
                best.match_method,
                best.time_delta_hours,
            )
            return None

        logger.info(
            "Matching OK '{}' → '{}' vs '{}' (conf={:.0%}, method={}, Δt={:.1f}h)",
            kalshi_market.ticker,
            best.home_team,
            best.away_team,
            best.confidence,
            best.match_method,
            best.time_delta_hours,
        )
        return best

    # -- Internal helpers -----------------------------------------------------

    def _score_candidate(
        self,
        kalshi_market: KalshiMarket,
        tokens: list[str],
        game: SportsbookGame,
        sport: str,
    ) -> Optional[MatchResult]:
        """Score one sportsbook game as a candidate match."""

        # 1. Time window check
        time_ok, time_delta = self._check_time_window(kalshi_market, game)
        if not time_ok:
            return None

        # 2. Team name matching
        yes_side, yes_team, team_conf, method = self._match_teams(tokens, game, sport)
        if yes_side == YesSide.UNKNOWN and team_conf == 0.0:
            return None

        # 3. Final confidence: blend team match + time proximity
        # Time factor: full score if within 1h, decays linearly to 0 at window edge
        time_factor = max(0.0, 1.0 - time_delta / self._time_window_hours)
        confidence = team_conf * 0.85 + time_factor * 0.15

        return MatchResult(
            kalshi_ticker=kalshi_market.ticker,
            sportsbook_game_id=game.game_id,
            provider=game.provider,
            home_team=game.home_team,
            away_team=game.away_team,
            yes_side=yes_side,
            yes_team=yes_team,
            sportsbook_commence_time=game.commence_time.isoformat(),
            time_delta_hours=round(time_delta, 2),
            confidence=round(confidence, 3),
            match_method=method,
        )

    def _check_time_window(
        self,
        kalshi_market: KalshiMarket,
        game: SportsbookGame,
    ) -> tuple[bool, float]:
        """Check if game commence time is within the window of Kalshi close time.

        Returns:
            (passes, abs_delta_hours)
        """
        if not kalshi_market.close_time:
            return False, 999.0

        try:
            close_str = kalshi_market.close_time
            if close_str.endswith("Z"):
                close_str = close_str[:-1] + "+00:00"
            kalshi_close = datetime.fromisoformat(close_str)
            if kalshi_close.tzinfo is None:
                kalshi_close = kalshi_close.replace(tzinfo=timezone.utc)

            delta = abs((game.commence_time - kalshi_close).total_seconds()) / 3600.0
            return delta <= self._time_window_hours, delta
        except Exception:
            return False, 999.0

    def _match_teams(
        self,
        tokens: list[str],
        game: SportsbookGame,
        sport: str,
    ) -> tuple[YesSide, str, float, MatchMethod]:
        """Attempt to match ticker tokens to game teams.

        Returns:
            (yes_side, yes_team_full_name, confidence, method)
        """
        norm = self._normalizer

        # Try exact lookup: token → canonical name → game team list
        for token in tokens:
            canonical = norm.ticker_to_name(sport, token)
            if canonical is None:
                continue

            canonical_lower = canonical.lower()
            home_lower = game.home_team.lower()
            away_lower = game.away_team.lower()

            # Direct exact match
            if canonical_lower == home_lower or canonical_lower in home_lower:
                return YesSide.HOME, game.home_team, 1.0, MatchMethod.EXACT
            if canonical_lower == away_lower or canonical_lower in away_lower:
                return YesSide.AWAY, game.away_team, 1.0, MatchMethod.EXACT

            # Partial: canonical keyword in game team name
            canonical_words = canonical_lower.split()
            for word in canonical_words:
                if len(word) > 3:
                    if word in home_lower:
                        return YesSide.HOME, game.home_team, 0.90, MatchMethod.EXACT
                    if word in away_lower:
                        return YesSide.AWAY, game.away_team, 0.90, MatchMethod.EXACT

        # Fuzzy fallback: match sportsbook names against lookup
        for side, team_name in ((YesSide.HOME, game.home_team), (YesSide.AWAY, game.away_team)):
            result = norm.fuzzy_match_name(sport, team_name, threshold=self._fuzzy_threshold)
            if result is None:
                continue
            _code, _canon, score = result
            if _code.upper() in [t.upper() for t in tokens]:
                return side, team_name, score * 0.90, MatchMethod.FUZZY

        # Also try: match token directly (as a word) in game team names
        for token in tokens:
            token_lower = token.lower()
            if len(token_lower) >= 2:
                if token_lower in game.home_team.lower():
                    return YesSide.HOME, game.home_team, 0.70, MatchMethod.FUZZY
                if token_lower in game.away_team.lower():
                    return YesSide.AWAY, game.away_team, 0.70, MatchMethod.FUZZY

        return YesSide.UNKNOWN, "", 0.0, MatchMethod.UNMATCHED
