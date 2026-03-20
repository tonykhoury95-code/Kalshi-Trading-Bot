"""
The Odds API provider adapter.

API reference: https://the-odds-api.com/liveapi/guides/v4/

Environment variable: THE_ODDS_API_KEY

Sport key mapping (Odds API → our Sport code):
    basketball_nba              → NBA
    americanfootball_nfl        → NFL
    baseball_mlb                → MLB
    icehockey_nhl               → NHL
    mma_mixed_martial_arts      → UFC
    soccer_usa_mls              → SOC
    tennis_atp_french_open      → TEN  (etc.)
"""

from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

from src.sportsbook.base import HealthStatus, OddsProvider
from src.sportsbook.models import (
    BookmakerLines,
    MarketLine,
    MarketType,
    Outcome,
    SportsbookGame,
)


# ---------------------------------------------------------------------------
# Sport key mapping
# ---------------------------------------------------------------------------

_OUR_TO_PROVIDER: dict[str, list[str]] = {
    "NBA":  ["basketball_nba"],
    "NFL":  ["americanfootball_nfl"],
    "MLB":  ["baseball_mlb"],
    "NHL":  ["icehockey_nhl"],
    "UFC":  ["mma_mixed_martial_arts"],
    "SOC":  ["soccer_usa_mls", "soccer_epl", "soccer_uefa_champs_league"],
    "TEN":  [
        "tennis_atp_french_open",
        "tennis_wta_french_open",
        "tennis_atp_wimbledon",
        "tennis_wta_wimbledon",
        "tennis_atp_us_open",
        "tennis_wta_us_open",
        "tennis_atp_aus_open",
        "tennis_wta_aus_open",
    ],
    "GOLF": ["golf_masters_tournament_winner", "golf_pga_championship_winner"],
}

_PROVIDER_TO_OUR: dict[str, str] = {}
for _our, _keys in _OUR_TO_PROVIDER.items():
    for _k in _keys:
        _PROVIDER_TO_OUR[_k] = _our

_MARKET_KEY_MAP: dict[str, MarketType] = {
    "h2h":    MarketType.MONEYLINE,
    "spreads": MarketType.SPREAD,
    "totals":  MarketType.TOTAL,
}

DEFAULT_BOOKMAKERS = ["fanduel", "draftkings", "betmgm", "caesars"]
DEFAULT_MARKETS = ["h2h", "spreads", "totals"]
BASE_URL = "https://api.the-odds-api.com/v4"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class TheOddsAPIProvider(OddsProvider):
    """Odds provider backed by The Odds API (https://the-odds-api.com).

    Args:
        api_key: The Odds API key (``THE_ODDS_API_KEY`` env var).
        default_bookmakers: Bookmaker keys to request when none specified.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        default_bookmakers: Optional[list[str]] = None,
        markets: Optional[list[str]] = None,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._default_bookmakers = default_bookmakers or DEFAULT_BOOKMAKERS
        self._markets = markets or DEFAULT_MARKETS
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        # Quota tracking (updated from response headers)
        self._requests_remaining: Optional[int] = None
        self._requests_used: Optional[int] = None

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=self._timeout)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("TheOddsAPIProvider not connected. Call connect() first.")
        return self._client

    # -- OddsProvider interface -----------------------------------------------

    @property
    def name(self) -> str:
        return "the_odds_api"

    @property
    def requests_remaining(self) -> Optional[int]:
        """Last known API quota remaining, updated after each request."""
        return self._requests_remaining

    async def health_check(self) -> HealthStatus:
        """Verify the API key is valid by calling GET /sports.

        This is a low-cost endpoint (~1 quota unit) that confirms auth works
        and returns the list of active sports.
        """
        try:
            response = await self._http.get(
                "/sports",
                params={"apiKey": self._api_key, "all": "false"},
            )
            response.raise_for_status()
            sports = response.json()
            remaining = response.headers.get("x-requests-remaining")
            used = response.headers.get("x-requests-used")
            self._requests_remaining = int(remaining) if remaining else None
            self._requests_used = int(used) if used else None

            active_keys = [s["key"] for s in sports if isinstance(s, dict) and s.get("active")]
            logger.info(
                "TheOddsAPI health check OK — {} active sports, {} requests remaining",
                len(active_keys),
                self._requests_remaining or "?",
            )
            return HealthStatus(
                ok=True,
                provider=self.name,
                message=f"{len(active_keys)} active sports available",
                requests_remaining=self._requests_remaining,
                requests_used=self._requests_used,
                details={"active_sport_keys": active_keys[:10]},
            )
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code}"
            if exc.response.status_code == 401:
                msg = "Invalid API key (401 Unauthorized)"
            elif exc.response.status_code == 429:
                msg = "Quota exhausted (429 Too Many Requests)"
            logger.error("TheOddsAPI health check FAILED: {}", msg)
            return HealthStatus(ok=False, provider=self.name, message=msg)
        except Exception as exc:
            logger.error("TheOddsAPI health check error: {}", exc)
            return HealthStatus(ok=False, provider=self.name, message=str(exc))

    @property
    def supported_sports(self) -> list[str]:
        return list(_OUR_TO_PROVIDER.keys())

    async def get_games(
        self,
        sport: str,
        bookmakers: Optional[list[str]] = None,
    ) -> list[SportsbookGame]:
        """Fetch upcoming/live games for a sport from The Odds API.

        Iterates over all provider-side sport keys for the given sport code
        and merges results.

        Args:
            sport: Our sport code, e.g. ``"NBA"``.
            bookmakers: Bookmaker keys to request. Defaults to FanDuel +
                        DraftKings + BetMGM + Caesars.

        Returns:
            List of normalised :class:`SportsbookGame` objects.
        """
        sport_upper = sport.upper()
        provider_keys = _OUR_TO_PROVIDER.get(sport_upper)
        if not provider_keys:
            logger.warning("TheOddsAPI: unsupported sport '{}'", sport_upper)
            return []

        bk_list = bookmakers or self._default_bookmakers
        bk_param = ",".join(bk_list)
        games: list[SportsbookGame] = []

        for sport_key in provider_keys:
            try:
                raw = await self._fetch_odds(sport_key, bk_param)
                for item in raw:
                    game = self._parse_game(item, sport_upper, sport_key)
                    if game:
                        games.append(game)
            except Exception as exc:
                logger.error(
                    "TheOddsAPI: error fetching {} ({}): {}", sport_upper, sport_key, exc
                )

        logger.info("TheOddsAPI: {} {} games fetched", len(games), sport_upper)
        return games

    # -- Internal helpers -----------------------------------------------------

    async def _fetch_odds(self, sport_key: str, bookmakers_param: str) -> list[dict]:
        """Make a single GET /sports/{sport_key}/odds call.

        Requests all configured market types (h2h, spreads, totals) in one
        call to minimise quota usage.
        """
        markets_param = ",".join(self._markets)
        params = {
            "apiKey": self._api_key,
            "regions": "us",
            "markets": markets_param,
            "oddsFormat": "american",
            "bookmakers": bookmakers_param,
        }
        path = f"/sports/{sport_key}/odds"
        logger.debug("TheOddsAPI GET {} (markets={})", path, markets_param)
        response = await self._http.get(path, params=params)
        response.raise_for_status()
        data = response.json()

        remaining = response.headers.get("x-requests-remaining")
        used = response.headers.get("x-requests-used")
        if remaining is not None:
            self._requests_remaining = int(remaining)
        if used is not None:
            self._requests_used = int(used)

        logger.debug(
            "TheOddsAPI {}: {} games, {} requests remaining",
            sport_key,
            len(data) if isinstance(data, list) else 0,
            self._requests_remaining or "?",
        )

        if self._requests_remaining is not None and self._requests_remaining < 10:
            logger.warning(
                "TheOddsAPI quota low: {} requests remaining", self._requests_remaining
            )

        return data if isinstance(data, list) else []

    @staticmethod
    def _parse_game(
        raw: dict, sport: str, sport_key: str
    ) -> Optional[SportsbookGame]:
        """Parse a single raw game dict into a SportsbookGame."""
        try:
            game_id = raw.get("id", "")
            if not game_id:
                return None

            commence_str = raw.get("commence_time", "")
            if not commence_str:
                return None
            if commence_str.endswith("Z"):
                commence_str = commence_str[:-1] + "+00:00"
            commence_time = datetime.fromisoformat(commence_str)
            if commence_time.tzinfo is None:
                commence_time = commence_time.replace(tzinfo=timezone.utc)

            home_team = raw.get("home_team", "")
            away_team = raw.get("away_team", "")
            if not home_team or not away_team:
                return None

            bookmakers: list[BookmakerLines] = []
            for bk_raw in raw.get("bookmakers", []):
                bk = _parse_bookmaker(bk_raw)
                if bk:
                    bookmakers.append(bk)

            return SportsbookGame(
                game_id=game_id,
                provider="the_odds_api",
                sport=sport,
                sport_key=sport_key,
                commence_time=commence_time,
                home_team=home_team,
                away_team=away_team,
                bookmakers=bookmakers,
            )
        except Exception as exc:
            logger.debug("TheOddsAPI: failed to parse game: {}", exc)
            return None


def _parse_bookmaker(raw: dict) -> Optional[BookmakerLines]:
    key = raw.get("key", "")
    title = raw.get("title", key)
    markets: list[MarketLine] = []

    for mkt_raw in raw.get("markets", []):
        mkt = _parse_market(mkt_raw)
        if mkt:
            markets.append(mkt)

    if not markets:
        return None

    last_update_str = raw.get("last_update")
    last_update: Optional[datetime] = None
    if last_update_str:
        try:
            if last_update_str.endswith("Z"):
                last_update_str = last_update_str[:-1] + "+00:00"
            last_update = datetime.fromisoformat(last_update_str)
        except Exception:
            pass

    return BookmakerLines(key=key, title=title, markets=markets, last_updated=last_update)


def _parse_market(raw: dict) -> Optional[MarketLine]:
    key = raw.get("key", "")
    market_type = _MARKET_KEY_MAP.get(key)
    if market_type is None:
        return None

    outcomes: list[Outcome] = []
    for oc in raw.get("outcomes", []):
        name = oc.get("name", "")
        price = oc.get("price")
        point = oc.get("point")
        if not name or price is None:
            continue
        try:
            outcomes.append(
                Outcome(
                    name=name,
                    price_american=int(price),
                    point=float(point) if point is not None else None,
                )
            )
        except Exception:
            continue

    if not outcomes:
        return None

    last_update_str = raw.get("last_update")
    last_update: Optional[datetime] = None
    if last_update_str:
        try:
            if last_update_str.endswith("Z"):
                last_update_str = last_update_str[:-1] + "+00:00"
            last_update = datetime.fromisoformat(last_update_str)
        except Exception:
            pass

    return MarketLine(market_type=market_type, outcomes=outcomes, last_updated=last_update)
