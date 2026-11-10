"""The curated competition catalogue linking Cricsheet archives to ESPN league ids."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Competition:
    """One supported competition archive."""

    code: str
    name: str
    gender: str | None
    team_type: str
    match_format: str
    region: str
    espn_league_id: int | None = None
    points_for_win: int = 2

    @property
    def cricsheet_url(self) -> str:
        """Cricsheet's canonical download URL for this archive."""
        return f"https://cricsheet.org/downloads/{self.code}_json.zip"


COMPETITIONS: tuple[Competition, ...] = (
    Competition("tests_male", "Men's Test matches", "male", "international", "Test", "International"),
    Competition("tests_female", "Women's Test matches", "female", "international", "Test", "International"),
    Competition("odis_male", "Men's One-Day Internationals", "male", "international", "ODI", "International"),
    Competition(
        "odis_female", "Women's One-Day Internationals", "female", "international", "ODI", "International"
    ),
    Competition("t20s_male", "Men's T20 Internationals", "male", "international", "T20I", "International"),
    Competition(
        "t20s_female", "Women's T20 Internationals", "female", "international", "T20I", "International"
    ),
    Competition(
        "icc_mens_cricket_world_cup",
        "ICC Men's Cricket World Cup",
        "male",
        "international",
        "ODI",
        "International",
        8039,
    ),
    Competition(
        "icc_mens_t20_world_cup",
        "ICC Men's T20 World Cup",
        "male",
        "international",
        "T20I",
        "International",
        8604,
    ),
    Competition(
        "icc_womens_cricket_world_cup",
        "ICC Women's Cricket World Cup",
        "female",
        "international",
        "ODI",
        "International",
    ),
    Competition(
        "icc_womens_t20_world_cup",
        "ICC Women's T20 World Cup",
        "female",
        "international",
        "T20I",
        "International",
    ),
    Competition("ipl", "Indian Premier League", "male", "club", "T20", "India", 8048),
    Competition("wpl", "Women's Premier League", "female", "club", "T20", "India", 21282),
    Competition("bbl", "Big Bash League", "male", "club", "T20", "Australia", 8044),
    Competition("wbb", "Women's Big Bash League", "female", "club", "T20", "Australia", 21284),
    Competition("hnd_male", "The Hundred (men)", "male", "club", "100-ball", "England and Wales", 19601, 4),
    Competition(
        "hnd_female", "The Hundred (women)", "female", "club", "100-ball", "England and Wales", 21376, 4
    ),
    Competition("cch", "County Championship", "male", "club", "First-class", "England and Wales", 8052),
    Competition("ntb", "T20 Blast", "male", "club", "T20", "England and Wales", 8053),
    Competition("rlc", "One-Day Cup (England)", "male", "club", "List A", "England and Wales"),
    Competition("rhf", "Rachael Heyhoe Flint Trophy", "female", "club", "List A", "England and Wales"),
    Competition("psl", "Pakistan Super League", "male", "club", "T20", "Pakistan", 8679),
    Competition("cpl", "Caribbean Premier League", "male", "club", "T20", "West Indies", 8623),
    Competition("wcl", "Women's Caribbean Premier League", "female", "club", "T20", "West Indies"),
    Competition("sat", "SA20", "male", "club", "T20", "South Africa"),
    Competition("mlc", "Major League Cricket", "male", "club", "T20", "United States"),
    Competition("ilt", "International League T20", "male", "club", "T20", "United Arab Emirates"),
    Competition("bpl", "Bangladesh Premier League", "male", "club", "T20", "Bangladesh", 8653),
    Competition("lpl", "Lanka Premier League", "male", "club", "T20", "Sri Lanka"),
    Competition("npl", "Nepal Premier League", "male", "club", "T20", "Nepal", 23081),
    Competition("ssm", "Super Smash", None, "club", "T20", "New Zealand"),
    Competition("ssh", "Sheffield Shield", "male", "club", "First-class", "Australia", 8043),
    Competition("odc", "One-Day Cup (Australia)", "male", "club", "List A", "Australia", 8626),
)

RECENT_ARCHIVES: tuple[str, ...] = ("recently_played_2", "recently_played_7", "recently_played_30")

BY_CODE: dict[str, Competition] = {competition.code: competition for competition in COMPETITIONS}
ARCHIVE_CHOICES: tuple[str, ...] = (*BY_CODE, *RECENT_ARCHIVES)
ESPN_CHOICES: tuple[str, ...] = tuple(
    competition.code for competition in COMPETITIONS if competition.espn_league_id is not None
)


def points_for_win(code: str) -> int:
    """Return the league-table points awarded for a win in this competition."""
    competition = BY_CODE.get(code)
    return competition.points_for_win if competition else 2


def competition_rows() -> list[dict]:
    """Rows for the ``competitions`` table."""
    return [
        {
            "competition": value.code,
            "name": value.name,
            "gender": value.gender,
            "team_type": value.team_type,
            "match_format": value.match_format,
            "region": value.region,
            "espn_league_id": value.espn_league_id,
            "points_for_win": value.points_for_win,
            "cricsheet_url": value.cricsheet_url,
        }
        for value in COMPETITIONS
    ]
