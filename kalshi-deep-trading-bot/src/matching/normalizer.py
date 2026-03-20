"""
Team / participant name normalizer.

Maps Kalshi ticker tokens (e.g. ``"BOS"``, ``"KC"``, ``"GSW"``) to
canonical team names and vice-versa.  Also provides fuzzy name matching
for cases where the sportsbook returns full team names that differ slightly
from our lookup table.

Usage
-----
    norm = TeamNormalizer()
    canonical = norm.ticker_to_name("NBA", "BOS")  # → "Boston Celtics"
    code = norm.name_to_code("NBA", "Boston Celtics")  # → "BOS"
    tokens = norm.extract_team_tokens("NBA-BOS-MIA-20240115-WIN", "NBA")
    # → ["BOS", "MIA"]
"""

import re
from difflib import SequenceMatcher
from typing import Optional


# ---------------------------------------------------------------------------
# Lookup tables
# Each entry: TICKER_CODE → (canonical_full_name, [aliases...])
# Aliases are tried for sportsbook name matching.
# ---------------------------------------------------------------------------

_NBA: dict[str, tuple[str, list[str]]] = {
    "ATL": ("Atlanta Hawks",              ["Atlanta", "Hawks"]),
    "BOS": ("Boston Celtics",             ["Boston", "Celtics"]),
    "BKN": ("Brooklyn Nets",              ["Brooklyn", "Nets"]),
    "CHA": ("Charlotte Hornets",          ["Charlotte", "Hornets"]),
    "CHI": ("Chicago Bulls",              ["Chicago", "Bulls"]),
    "CLE": ("Cleveland Cavaliers",        ["Cleveland", "Cavaliers", "Cavs"]),
    "DAL": ("Dallas Mavericks",           ["Dallas", "Mavericks", "Mavs"]),
    "DEN": ("Denver Nuggets",             ["Denver", "Nuggets"]),
    "DET": ("Detroit Pistons",            ["Detroit", "Pistons"]),
    "GSW": ("Golden State Warriors",      ["Golden State", "Warriors", "GS"]),
    "HOU": ("Houston Rockets",            ["Houston", "Rockets"]),
    "IND": ("Indiana Pacers",             ["Indiana", "Pacers"]),
    "LAC": ("Los Angeles Clippers",       ["LA Clippers", "Clippers"]),
    "LAL": ("Los Angeles Lakers",         ["LA Lakers", "Lakers"]),
    "MEM": ("Memphis Grizzlies",          ["Memphis", "Grizzlies"]),
    "MIA": ("Miami Heat",                 ["Miami", "Heat"]),
    "MIL": ("Milwaukee Bucks",            ["Milwaukee", "Bucks"]),
    "MIN": ("Minnesota Timberwolves",     ["Minnesota", "Timberwolves", "Wolves"]),
    "NOP": ("New Orleans Pelicans",       ["New Orleans", "Pelicans", "NO"]),
    "NYK": ("New York Knicks",            ["New York", "Knicks", "NY"]),
    "OKC": ("Oklahoma City Thunder",      ["Oklahoma City", "Thunder"]),
    "ORL": ("Orlando Magic",              ["Orlando", "Magic"]),
    "PHI": ("Philadelphia 76ers",         ["Philadelphia", "76ers", "Sixers"]),
    "PHX": ("Phoenix Suns",               ["Phoenix", "Suns"]),
    "POR": ("Portland Trail Blazers",     ["Portland", "Trail Blazers", "Blazers"]),
    "SAC": ("Sacramento Kings",           ["Sacramento", "Kings"]),
    "SAS": ("San Antonio Spurs",          ["San Antonio", "Spurs", "SA"]),
    "TOR": ("Toronto Raptors",            ["Toronto", "Raptors"]),
    "UTA": ("Utah Jazz",                  ["Utah", "Jazz"]),
    "WAS": ("Washington Wizards",         ["Washington", "Wizards"]),
}

_NFL: dict[str, tuple[str, list[str]]] = {
    "ARI": ("Arizona Cardinals",          ["Arizona", "Cardinals"]),
    "ATL": ("Atlanta Falcons",            ["Atlanta", "Falcons"]),
    "BAL": ("Baltimore Ravens",           ["Baltimore", "Ravens"]),
    "BUF": ("Buffalo Bills",              ["Buffalo", "Bills"]),
    "CAR": ("Carolina Panthers",          ["Carolina", "Panthers"]),
    "CHI": ("Chicago Bears",              ["Chicago", "Bears"]),
    "CIN": ("Cincinnati Bengals",         ["Cincinnati", "Bengals"]),
    "CLE": ("Cleveland Browns",           ["Cleveland", "Browns"]),
    "DAL": ("Dallas Cowboys",             ["Dallas", "Cowboys"]),
    "DEN": ("Denver Broncos",             ["Denver", "Broncos"]),
    "DET": ("Detroit Lions",              ["Detroit", "Lions"]),
    "GB":  ("Green Bay Packers",          ["Green Bay", "Packers"]),
    "HOU": ("Houston Texans",             ["Houston", "Texans"]),
    "IND": ("Indianapolis Colts",         ["Indianapolis", "Colts"]),
    "JAX": ("Jacksonville Jaguars",       ["Jacksonville", "Jaguars", "JAC"]),
    "KC":  ("Kansas City Chiefs",         ["Kansas City", "Chiefs"]),
    "LAC": ("Los Angeles Chargers",       ["LA Chargers", "Chargers"]),
    "LAR": ("Los Angeles Rams",           ["LA Rams", "Rams"]),
    "LV":  ("Las Vegas Raiders",          ["Las Vegas", "Raiders", "OAK"]),
    "MIA": ("Miami Dolphins",             ["Miami", "Dolphins"]),
    "MIN": ("Minnesota Vikings",          ["Minnesota", "Vikings"]),
    "NE":  ("New England Patriots",       ["New England", "Patriots"]),
    "NO":  ("New Orleans Saints",         ["New Orleans", "Saints"]),
    "NYG": ("New York Giants",            ["NY Giants", "Giants"]),
    "NYJ": ("New York Jets",              ["NY Jets", "Jets"]),
    "PHI": ("Philadelphia Eagles",        ["Philadelphia", "Eagles"]),
    "PIT": ("Pittsburgh Steelers",        ["Pittsburgh", "Steelers"]),
    "SEA": ("Seattle Seahawks",           ["Seattle", "Seahawks"]),
    "SF":  ("San Francisco 49ers",        ["San Francisco", "49ers", "Niners"]),
    "TB":  ("Tampa Bay Buccaneers",       ["Tampa Bay", "Buccaneers", "Bucs"]),
    "TEN": ("Tennessee Titans",           ["Tennessee", "Titans"]),
    "WAS": ("Washington Commanders",      ["Washington", "Commanders", "Football Team"]),
}

_MLB: dict[str, tuple[str, list[str]]] = {
    "ARI": ("Arizona Diamondbacks",       ["Arizona", "Diamondbacks", "D-backs"]),
    "ATL": ("Atlanta Braves",             ["Atlanta", "Braves"]),
    "BAL": ("Baltimore Orioles",          ["Baltimore", "Orioles"]),
    "BOS": ("Boston Red Sox",             ["Boston", "Red Sox"]),
    "CHC": ("Chicago Cubs",               ["Chicago Cubs", "Cubs"]),
    "CWS": ("Chicago White Sox",          ["Chicago White Sox", "White Sox"]),
    "CIN": ("Cincinnati Reds",            ["Cincinnati", "Reds"]),
    "CLE": ("Cleveland Guardians",        ["Cleveland", "Guardians"]),
    "COL": ("Colorado Rockies",           ["Colorado", "Rockies"]),
    "DET": ("Detroit Tigers",             ["Detroit", "Tigers"]),
    "HOU": ("Houston Astros",             ["Houston", "Astros"]),
    "KC":  ("Kansas City Royals",         ["Kansas City", "Royals"]),
    "LAA": ("Los Angeles Angels",         ["LA Angels", "Angels"]),
    "LAD": ("Los Angeles Dodgers",        ["LA Dodgers", "Dodgers"]),
    "MIA": ("Miami Marlins",              ["Miami", "Marlins"]),
    "MIL": ("Milwaukee Brewers",          ["Milwaukee", "Brewers"]),
    "MIN": ("Minnesota Twins",            ["Minnesota", "Twins"]),
    "NYM": ("New York Mets",              ["NY Mets", "Mets"]),
    "NYY": ("New York Yankees",           ["NY Yankees", "Yankees"]),
    "OAK": ("Oakland Athletics",          ["Oakland", "Athletics", "A's"]),
    "PHI": ("Philadelphia Phillies",      ["Philadelphia", "Phillies"]),
    "PIT": ("Pittsburgh Pirates",         ["Pittsburgh", "Pirates"]),
    "SD":  ("San Diego Padres",           ["San Diego", "Padres"]),
    "SEA": ("Seattle Mariners",           ["Seattle", "Mariners"]),
    "SF":  ("San Francisco Giants",       ["San Francisco", "Giants"]),
    "STL": ("St. Louis Cardinals",        ["St Louis", "Cardinals"]),
    "TB":  ("Tampa Bay Rays",             ["Tampa Bay", "Rays"]),
    "TEX": ("Texas Rangers",              ["Texas", "Rangers"]),
    "TOR": ("Toronto Blue Jays",          ["Toronto", "Blue Jays"]),
    "WAS": ("Washington Nationals",       ["Washington", "Nationals"]),
}

_NHL: dict[str, tuple[str, list[str]]] = {
    "ANA": ("Anaheim Ducks",              ["Anaheim", "Ducks"]),
    "ARI": ("Arizona Coyotes",            ["Arizona", "Coyotes"]),
    "BOS": ("Boston Bruins",              ["Boston", "Bruins"]),
    "BUF": ("Buffalo Sabres",             ["Buffalo", "Sabres"]),
    "CAR": ("Carolina Hurricanes",        ["Carolina", "Hurricanes", "Canes"]),
    "CBJ": ("Columbus Blue Jackets",      ["Columbus", "Blue Jackets"]),
    "CGY": ("Calgary Flames",             ["Calgary", "Flames"]),
    "CHI": ("Chicago Blackhawks",         ["Chicago", "Blackhawks"]),
    "COL": ("Colorado Avalanche",         ["Colorado", "Avalanche", "Avs"]),
    "DAL": ("Dallas Stars",               ["Dallas", "Stars"]),
    "DET": ("Detroit Red Wings",          ["Detroit", "Red Wings"]),
    "EDM": ("Edmonton Oilers",            ["Edmonton", "Oilers"]),
    "FLA": ("Florida Panthers",           ["Florida", "Panthers"]),
    "LAK": ("Los Angeles Kings",          ["LA Kings", "Kings"]),
    "MIN": ("Minnesota Wild",             ["Minnesota", "Wild"]),
    "MTL": ("Montreal Canadiens",         ["Montreal", "Canadiens", "Habs"]),
    "NJD": ("New Jersey Devils",          ["New Jersey", "Devils", "NJ"]),
    "NSH": ("Nashville Predators",        ["Nashville", "Predators", "Preds"]),
    "NYI": ("New York Islanders",         ["NY Islanders", "Islanders"]),
    "NYR": ("New York Rangers",           ["NY Rangers", "Rangers"]),
    "OTT": ("Ottawa Senators",            ["Ottawa", "Senators", "Sens"]),
    "PHI": ("Philadelphia Flyers",        ["Philadelphia", "Flyers"]),
    "PIT": ("Pittsburgh Penguins",        ["Pittsburgh", "Penguins", "Pens"]),
    "SEA": ("Seattle Kraken",             ["Seattle", "Kraken"]),
    "SJS": ("San Jose Sharks",            ["San Jose", "Sharks", "SJ"]),
    "STL": ("St. Louis Blues",            ["St Louis", "Blues"]),
    "TBL": ("Tampa Bay Lightning",        ["Tampa Bay", "Lightning"]),
    "TOR": ("Toronto Maple Leafs",        ["Toronto", "Maple Leafs", "Leafs"]),
    "UTA": ("Utah Hockey Club",           ["Utah"]),
    "VAN": ("Vancouver Canucks",          ["Vancouver", "Canucks"]),
    "VGK": ("Vegas Golden Knights",       ["Vegas", "Golden Knights", "VGS"]),
    "WPG": ("Winnipeg Jets",              ["Winnipeg", "Jets"]),
    "WSH": ("Washington Capitals",        ["Washington", "Capitals", "Caps"]),
}

_SOC: dict[str, tuple[str, list[str]]] = {
    "LAFC":  ("LAFC",                     ["Los Angeles FC"]),
    "LAG":   ("LA Galaxy",                ["Los Angeles Galaxy", "Galaxy"]),
    "SEA":   ("Seattle Sounders",         ["Seattle", "Sounders"]),
    "POR":   ("Portland Timbers",         ["Portland", "Timbers"]),
    "NYC":   ("New York City FC",         ["NYCFC", "NYC FC"]),
    "NYRB":  ("New York Red Bulls",       ["Red Bulls", "NY Red Bulls"]),
    "ATL":   ("Atlanta United",           ["Atlanta"]),
    "MIA":   ("Inter Miami",              ["Miami", "Inter Miami CF"]),
    "CHI":   ("Chicago Fire",             ["Chicago", "Fire"]),
    "CLB":   ("Columbus Crew",            ["Columbus", "Crew"]),
    "DC":    ("DC United",                ["D.C. United", "Washington"]),
    "NE":    ("New England Revolution",   ["New England", "Revolution"]),
    "PHI":   ("Philadelphia Union",       ["Philadelphia", "Union"]),
    "TOR":   ("Toronto FC",               ["Toronto"]),
    "MTL":   ("CF Montreal",              ["Montreal", "Impact"]),
    "VAN":   ("Vancouver Whitecaps",      ["Vancouver", "Whitecaps"]),
    "MIN":   ("Minnesota United",         ["Minnesota", "Loons"]),
    "COL":   ("Colorado Rapids",          ["Colorado", "Rapids"]),
    "DAL":   ("FC Dallas",                ["Dallas"]),
    "HOU":   ("Houston Dynamo",           ["Houston", "Dynamo"]),
    "KC":    ("Sporting Kansas City",     ["Kansas City", "Sporting KC", "SKC"]),
    "RSL":   ("Real Salt Lake",           ["Salt Lake", "RSL"]),
    "SJ":    ("San Jose Earthquakes",     ["San Jose", "Earthquakes"]),
    "SKC":   ("Sporting Kansas City",     ["Kansas City", "Sporting KC"]),
    "STL":   ("St. Louis City SC",        ["St Louis", "City SC"]),
    "NSH":   ("Nashville SC",             ["Nashville"]),
    "CLT":   ("Charlotte FC",             ["Charlotte"]),
    "SLC":   ("St. Louis City",           ["St Louis"]),
    "AUS":   ("Austin FC",                ["Austin"]),
    "CIN":   ("FC Cincinnati",            ["Cincinnati"]),
    "ORL":   ("Orlando City",             ["Orlando"]),
    "NE":    ("New England Revolution",   ["Revolution"]),
    "SAN":   ("San Diego FC",             ["San Diego"]),
}

_SPORT_LOOKUP: dict[str, dict[str, tuple[str, list[str]]]] = {
    "NBA":  _NBA,
    "NFL":  _NFL,
    "MLB":  _MLB,
    "NHL":  _NHL,
    "SOC":  _SOC,
}

# Tokens that look like team codes but are actually suffixes / dates
_IGNORE_TOKENS = frozenset({
    "WIN", "WINNER", "CARD", "FIGHT", "BOUT", "MAIN", "EVENT",
    "TIMEV2", "SERIES", "SERIES1", "PLAYOFF",
    "FINAL", "SEMIFINAL", "QTR",
    "2024", "2025", "2026", "25", "24", "26",
})

_DATE_PATTERN = re.compile(r"^\d{4,8}$")   # pure numeric → date


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TeamNormalizer:
    """Map between Kalshi ticker tokens and full team names."""

    def ticker_to_name(self, sport: str, token: str) -> Optional[str]:
        """Return canonical full name for a Kalshi ticker token.

        Args:
            sport: Sport code, e.g. ``"NBA"``.
            token: Ticker segment, e.g. ``"BOS"``.

        Returns:
            Full team name (e.g. ``"Boston Celtics"``) or ``None``.
        """
        lookup = _SPORT_LOOKUP.get(sport.upper(), {})
        entry = lookup.get(token.upper())
        return entry[0] if entry else None

    def name_to_code(self, sport: str, full_name: str) -> Optional[str]:
        """Find the Kalshi ticker code for a sportsbook team name.

        Checks canonical name and all aliases (case-insensitive).

        Args:
            sport: Sport code.
            full_name: Sportsbook team name (full or partial).

        Returns:
            Ticker code (e.g. ``"BOS"``) or ``None``.
        """
        lookup = _SPORT_LOOKUP.get(sport.upper(), {})
        name_lower = full_name.lower()
        for code, (canonical, aliases) in lookup.items():
            if canonical.lower() == name_lower:
                return code
            for alias in aliases:
                if alias.lower() == name_lower:
                    return code
        return None

    def fuzzy_match_name(
        self, sport: str, full_name: str, threshold: float = 0.75
    ) -> Optional[tuple[str, str, float]]:
        """Fuzzy-match a sportsbook team name against the lookup table.

        Args:
            sport: Sport code.
            full_name: Sportsbook team name to match.
            threshold: Minimum similarity ratio (0–1).

        Returns:
            ``(code, canonical_name, similarity)`` or ``None`` if below threshold.
        """
        lookup = _SPORT_LOOKUP.get(sport.upper(), {})
        name_lower = full_name.lower()
        best_code: Optional[str] = None
        best_name: Optional[str] = None
        best_score = 0.0

        for code, (canonical, aliases) in lookup.items():
            candidates = [canonical] + aliases
            for candidate in candidates:
                score = SequenceMatcher(None, name_lower, candidate.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best_code = code
                    best_name = canonical

        if best_code and best_score >= threshold:
            return best_code, best_name, best_score  # type: ignore[return-value]
        return None

    def extract_team_tokens(self, ticker: str, sport: str) -> list[str]:
        """Extract team code tokens from a Kalshi market ticker.

        Strips the sport prefix and known suffix tokens, leaving only the
        parts that look like team abbreviations.

        Examples::

            "NBA-BOS-MIA-20240115-WIN"  → ["BOS", "MIA"]
            "NFL-KC-BUF-2025"           → ["KC", "BUF"]
            "NBA-CELTICS-25"            → []  (full-name style, not abbrev)
            "UFC-305-CARD"              → []  (no team codes)

        Args:
            ticker: Kalshi market ticker string.
            sport: Sport code for lookup-table validation.

        Returns:
            List of valid team code tokens (may be empty).
        """
        upper = ticker.upper()
        # Remove known sport prefixes (including KX prefix)
        for prefix in (f"KX{sport.upper()}-", f"{sport.upper()}-"):
            if upper.startswith(prefix):
                upper = upper[len(prefix):]
                break

        parts = upper.split("-")
        lookup = _SPORT_LOOKUP.get(sport.upper(), {})
        tokens: list[str] = []

        for part in parts:
            if not part:
                continue
            if part in _IGNORE_TOKENS:
                continue
            if _DATE_PATTERN.match(part):
                continue
            if part in lookup:
                tokens.append(part)

        return tokens

    def normalize_for_fuzzy(self, name: str) -> str:
        """Strip punctuation and lowercase for fuzzy comparison."""
        return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
