"""Typed Arrow schemas, with SQL column comments, for every cricket result."""

from __future__ import annotations

import pyarrow as pa

from vgi_cricket.meta import field

RATE = pa.decimal128(7, 2)
NRR = pa.decimal128(6, 3)
POINTS = pa.decimal128(6, 1)
TIMESTAMP = pa.timestamp("us", tz="UTC")
I32 = pa.int32()
I64 = pa.int64()
STR = pa.string()
BOOL = pa.bool_()
DATE = pa.date32()

_COMPETITION = field("competition", STR, "Competition code from the competitions table, such as ipl.")
_SEASON = field("season", STR, "Cricsheet season label, such as 2026 or 2025/26.")
_MATCH_ID = field(
    "match_id",
    I64,
    "Match identifier (opaque number), equal to the ESPNcricinfo and ESPN event id.",
    nullable=False,
)

COMPETITIONS_SCHEMA = pa.schema(
    [
        field("competition", STR, "Competition code accepted by every Cricsheet function.", nullable=False),
        field("name", STR, "Competition name."),
        field("gender", STR, "male or female; NULL when the archive covers both."),
        field("team_type", STR, "international or club."),
        field("match_format", STR, "Format family: Test, ODI, T20I, T20, 100-ball, First-class, or List A."),
        field("region", STR, "Country or region the competition is played in."),
        field("espn_league_id", I32, "ESPN cricket league id used by schedule(); NULL when not mapped."),
        field("points_for_win", I32, "League-table points standings() awards for a win."),
        field("cricsheet_url", STR, "Cricsheet archive the competition's history is read from."),
    ]
)

MATCH_SCHEMA = pa.schema(
    [
        _MATCH_ID,
        _COMPETITION,
        field("event_name", STR, "Event or series name recorded by Cricsheet."),
        _SEASON,
        field("match_number", I32, "Ordinal number of the match within the event, when recorded."),
        field("stage", STR, "Knockout stage such as Final or Qualifier 1; NULL for league-stage matches."),
        field("event_group", STR, "Group label within the event, when recorded."),
        field("start_date", DATE, "First scheduled day of the match."),
        field("end_date", DATE, "Last scheduled day of the match."),
        field("match_type", STR, "Cricsheet match type: Test, ODI, T20, IT20, ODM, or MDM."),
        field("gender", STR, "male or female."),
        field("team_type", STR, "international or club."),
        field("scheduled_overs", I32, "Scheduled overs per innings for limited-overs matches."),
        field("balls_per_over", I32, "Balls per over; 5 for The Hundred's sets, otherwise 6."),
        field("team1", STR, "First team as listed by Cricsheet."),
        field("team2", STR, "Second team as listed by Cricsheet."),
        field("team1_score", STR, "team1 innings scores, for example 155/8 (20.0 ov); innings joined by &."),
        field("team2_score", STR, "team2 innings scores, for example 161/5 (18.0 ov); innings joined by &."),
        field("venue", STR, "Ground name as recorded for this match."),
        field("venue_id", STR, "Cricsheet venue identifier; joins to venues.venue_id."),
        field("city", STR, "City of the ground, when recorded."),
        field("toss_winner", STR, "Team that won the toss."),
        field("toss_decision", STR, "bat or field."),
        field("result", STR, "win, tie, draw, or no result."),
        field("winner", STR, "Winning team, including super-over and bowl-out winners of tied matches."),
        field("win_by_runs", I32, "Winning margin in runs, when won by runs."),
        field("win_by_wickets", I32, "Winning margin in wickets, when won by wickets."),
        field("win_by_innings", BOOL, "Whether a multi-day match was won by an innings."),
        field("decided_by", STR, "super over or bowl out when a tie was broken; otherwise NULL."),
        field("method", STR, "Result method such as D/L or Awarded, when one applied."),
        field("player_of_match", STR, "Player(s) of the match, comma-separated."),
        field("umpires", STR, "On-field umpires, comma-separated."),
    ]
)

INNINGS_SCHEMA = pa.schema(
    [
        _MATCH_ID,
        _COMPETITION,
        _SEASON,
        field("innings_number", I32, "Innings order within the match, starting at 1.", nullable=False),
        field("batting_team", STR, "Team batting in this innings."),
        field("bowling_team", STR, "Team bowling in this innings."),
        field("runs", I32, "Total runs including extras and penalty runs."),
        field("wickets", I32, "Wickets lost; retired-hurt and retired-not-out departures are excluded."),
        field("legal_balls", I32, "Legal deliveries faced (wides and no-balls excluded)."),
        field("overs", STR, "Overs faced in cricket notation, for example 18.4."),
        field("run_rate", RATE, "Runs per six legal balls."),
        field("extras", I32, "Extras conceded, including penalty runs."),
        field("byes", I32, "Byes."),
        field("leg_byes", I32, "Leg byes."),
        field("wides", I32, "Wide runs."),
        field("no_balls", I32, "No-ball runs."),
        field("penalties", I32, "Penalty runs awarded."),
        field("all_out", BOOL, "Whether the batting side was bowled out."),
        field("declared", BOOL, "Whether the innings was declared."),
        field("forfeited", BOOL, "Whether the innings was forfeited."),
        field("super_over", BOOL, "Whether this innings is a super over."),
        field("target_runs", I32, "Target set for this innings, when Cricsheet records one."),
    ]
)

BATTING_SCORECARD_SCHEMA = pa.schema(
    [
        _MATCH_ID,
        _COMPETITION,
        _SEASON,
        field("start_date", DATE, "First day of the match."),
        field("innings_number", I32, "Innings order within the match.", nullable=False),
        field("batting_team", STR, "Batter's team."),
        field("bowling_team", STR, "Opposing team."),
        field("batting_position", I32, "Order in which the batter came to the crease.", nullable=False),
        field("batter", STR, "Batter's name as recorded by Cricsheet.", nullable=False),
        field("batter_id", STR, "Cricsheet person identifier; joins to people.person_id."),
        field("runs", I32, "Runs scored off the bat."),
        field("balls", I32, "Balls faced (wides excluded, no-balls included)."),
        field("fours", I32, "Boundary fours."),
        field("sixes", I32, "Boundary sixes."),
        field("dots", I32, "Balls faced without scoring off the bat."),
        field("strike_rate", RATE, "Runs per 100 balls faced."),
        field("is_out", BOOL, "Whether the batter was dismissed (retired hurt counts as not out)."),
        field("dismissal_kind", STR, "Dismissal kind such as caught or run out; NULL when not out."),
        field("dismissed_by", STR, "Bowler credited with the dismissal, when the bowler is credited."),
        field("fielders", STR, "Fielders involved in the dismissal, comma-separated."),
    ]
)

BOWLING_SCORECARD_SCHEMA = pa.schema(
    [
        _MATCH_ID,
        _COMPETITION,
        _SEASON,
        field("start_date", DATE, "First day of the match."),
        field("innings_number", I32, "Innings order within the match.", nullable=False),
        field("bowling_team", STR, "Bowler's team."),
        field("batting_team", STR, "Team batting in the innings."),
        field("bowling_order", I32, "Order in which the bowler first bowled in the innings.", nullable=False),
        field("bowler", STR, "Bowler's name as recorded by Cricsheet.", nullable=False),
        field("bowler_id", STR, "Cricsheet person identifier; joins to people.person_id."),
        field("legal_balls", I32, "Legal deliveries bowled."),
        field("overs", STR, "Overs bowled in cricket notation, for example 3.4."),
        field("maidens", I32, "Complete overs conceding no runs charged to the bowler."),
        field("runs_conceded", I32, "Runs charged to the bowler (bat runs, wides, and no-balls)."),
        field("wickets", I32, "Wickets credited to the bowler."),
        field("economy", RATE, "Runs conceded per six legal balls."),
        field("dots", I32, "Legal deliveries from which no run was scored."),
        field("fours_conceded", I32, "Boundary fours conceded."),
        field("sixes_conceded", I32, "Boundary sixes conceded."),
        field("wides", I32, "Wide runs conceded."),
        field("no_balls", I32, "No-ball runs conceded."),
    ]
)

DELIVERIES_SCHEMA = pa.schema(
    [
        _MATCH_ID,
        _COMPETITION,
        _SEASON,
        field("innings_number", I32, "Innings order within the match.", nullable=False),
        field("super_over", BOOL, "Whether the delivery was bowled in a super over."),
        field("batting_team", STR, "Team batting."),
        field("bowling_team", STR, "Team bowling."),
        field("over_number", I32, "Over number, starting at 1.", nullable=False),
        field(
            "ball_in_over",
            I32,
            "Delivery sequence within the over, counting wides and no-balls.",
            nullable=False,
        ),
        field(
            "legal_ball_number", I32, "Running legal-ball count in the innings; NULL for wides and no-balls."
        ),
        field("batter", STR, "Batter on strike."),
        field("batter_id", STR, "Cricsheet person identifier of the batter."),
        field("non_striker", STR, "Non-striking batter."),
        field("bowler", STR, "Bowler."),
        field("bowler_id", STR, "Cricsheet person identifier of the bowler."),
        field("runs_batter", I32, "Runs off the bat."),
        field("runs_extras", I32, "Extra runs on the delivery."),
        field("runs_total", I32, "Total runs on the delivery."),
        field("wides", I32, "Wide runs."),
        field("no_balls", I32, "No-ball runs."),
        field("byes", I32, "Byes."),
        field("leg_byes", I32, "Leg byes."),
        field("penalty", I32, "Penalty runs."),
        field("is_legal", BOOL, "Whether the delivery counts toward the over."),
        field("is_boundary", BOOL, "Whether the batter hit a four or six."),
        field("is_wicket", BOOL, "Whether a wicket fell on the delivery."),
        field("player_out", STR, "Dismissed batter, when a wicket fell."),
        field("wicket_kind", STR, "Dismissal kind, when a wicket fell."),
        field("fielders", STR, "Fielders involved in the dismissal, comma-separated."),
    ]
)

STANDINGS_SCHEMA = pa.schema(
    [
        _COMPETITION,
        _SEASON,
        field("group_name", STR, "Group within the event; NULL for a single league table."),
        field("position", I32, "Computed table position: points, then net run rate, then wins."),
        field("team", STR, "Team name.", nullable=False),
        field("played", I32, "League-stage matches played."),
        field("won", I32, "Wins, including super-over and bowl-out wins."),
        field("lost", I32, "Losses, including super-over and bowl-out losses."),
        field("tied", I32, "Ties that were not broken by a super over or bowl out."),
        field("drawn", I32, "Draws (multi-day matches)."),
        field("no_result", I32, "No-result and abandoned matches."),
        field(
            "points", POINTS, "Computed points: points_for_win per win and half per tie, draw, or no result."
        ),
        field("net_run_rate", NRR, "Computed net run rate; an all-out side is charged its full allocation."),
        field("runs_for", I32, "Runs scored in NRR-eligible matches."),
        field("balls_for", I32, "Balls faced in NRR-eligible matches, after the all-out rule."),
        field("runs_against", I32, "Runs conceded in NRR-eligible matches."),
        field("balls_against", I32, "Balls bowled in NRR-eligible matches, after the all-out rule."),
    ]
)

BATTING_STATS_SCHEMA = pa.schema(
    [
        _COMPETITION,
        field("season", STR, "Requested season label; NULL for all seasons in the archive."),
        field("player_id", STR, "Cricsheet person identifier; joins to people.person_id."),
        field("player", STR, "Player name as recorded by Cricsheet.", nullable=False),
        field("teams", STR, "Teams the player appeared for, comma-separated."),
        field("matches", I32, "Matches in which the player was in the XI."),
        field("innings", I32, "Innings batted."),
        field("not_outs", I32, "Innings finished not out."),
        field("runs", I32, "Runs scored."),
        field("balls", I32, "Balls faced."),
        field("highest_score", I32, "Highest innings score."),
        field("highest_not_out", BOOL, "Whether the highest score was not out."),
        field("average", RATE, "Runs per dismissal; NULL without a dismissal."),
        field("strike_rate", RATE, "Runs per 100 balls faced."),
        field("fifties", I32, "Innings of 50 to 99."),
        field("hundreds", I32, "Innings of 100 or more."),
        field("ducks", I32, "Dismissals for zero."),
        field("fours", I32, "Boundary fours."),
        field("sixes", I32, "Boundary sixes."),
    ]
)

BOWLING_STATS_SCHEMA = pa.schema(
    [
        _COMPETITION,
        field("season", STR, "Requested season label; NULL for all seasons in the archive."),
        field("player_id", STR, "Cricsheet person identifier; joins to people.person_id."),
        field("player", STR, "Player name as recorded by Cricsheet.", nullable=False),
        field("teams", STR, "Teams the player bowled for, comma-separated."),
        field("matches", I32, "Matches in which the player bowled."),
        field("innings", I32, "Innings bowled in."),
        field("legal_balls", I32, "Legal deliveries bowled."),
        field("overs", STR, "Overs bowled in cricket notation."),
        field("maidens", I32, "Maiden overs."),
        field("runs_conceded", I32, "Runs charged to the bowler."),
        field("wickets", I32, "Wickets credited to the bowler."),
        field("best_figures", STR, "Best innings figures as wickets/runs, for example 5/21."),
        field("average", RATE, "Runs conceded per wicket."),
        field("economy", RATE, "Runs conceded per six legal balls."),
        field("strike_rate", RATE, "Legal balls per wicket."),
        field("four_wickets", I32, "Innings with exactly four wickets."),
        field("five_wickets", I32, "Innings with five or more wickets."),
        field("dots", I32, "Dot balls bowled."),
    ]
)

PEOPLE_SCHEMA = pa.schema(
    [
        field("person_id", STR, "Cricsheet person identifier.", nullable=False),
        field("name", STR, "Name as used on Cricsheet scorecards."),
        field("unique_name", STR, "Disambiguated unique name."),
        field(
            "key_cricinfo",
            I64,
            "ESPNcricinfo player identifier (opaque number), equal to the ESPN athlete id.",
        ),
        field("key_cricbuzz", STR, "Cricbuzz player id."),
        field("key_bcci", STR, "BCCI player id."),
        field("key_pulse", STR, "PulseLive (ICC/ECB) player id."),
        field("key_opta", STR, "Opta player id."),
        field("key_bigbash", STR, "Big Bash League player id."),
        field("key_cricketarchive", STR, "CricketArchive player id."),
        field("key_nvplay", STR, "NV Play player id."),
    ]
)

VENUES_SCHEMA = pa.schema(
    [
        field("venue_id", STR, "Cricsheet venue identifier.", nullable=False),
        field("name", STR, "Venue name as used in match files."),
        field("unique_name", STR, "Canonical venue name."),
        field("key_cricinfo", I64, "ESPNcricinfo ground identifier (opaque number)."),
    ]
)

ESPN_MATCH_SCHEMA = pa.schema(
    [
        field(
            "event_id",
            I64,
            "ESPN event identifier (opaque number), equal to the Cricsheet and ESPNcricinfo match id.",
            nullable=False,
        ),
        field("league_id", I32, "Numeric ESPN league (series) identifier."),
        field("league_name", STR, "ESPN league or series name."),
        field("season_year", I32, "ESPN season as a calendar year."),
        field("start_time", TIMESTAMP, "Scheduled start time in UTC."),
        field("end_time", TIMESTAMP, "Scheduled end time in UTC."),
        field("name", STR, "Match name, for example Royal Challengers Bengaluru v Gujarat Titans."),
        field("short_name", STR, "Abbreviated match name."),
        field("description", STR, "Match label such as Final or 9th Match."),
        field("match_class", STR, "ESPN match class such as Twenty20, ODI, or Test."),
        field("venue", STR, "Ground name."),
        field("city", STR, "Ground city."),
        field("country", STR, "Ground country."),
        field("state", STR, "pre (scheduled), in (live), or post (finished)."),
        field("status", STR, "Status text such as Result, Live, Stumps, or Scheduled."),
        field("summary", STR, "Result or situation summary, for example RCB won by 5 wkts."),
        field("home_team", STR, "Home (or second-listed) team."),
        field("home_team_id", I64, "ESPN team identifier (opaque number) of the home team."),
        field("home_score", STR, "Home team's score text."),
        field("away_team", STR, "Away (or first-listed) team."),
        field("away_team_id", I64, "ESPN team identifier (opaque number) of the away team."),
        field("away_score", STR, "Away team's score text."),
        field("winner", STR, "Winning team once decided."),
    ]
)

LIVE_BATTING_SCHEMA = pa.schema(
    [
        field("event_id", I64, "Numeric ESPN event identifier.", nullable=False),
        field("innings_number", I32, "Innings order within the match.", nullable=False),
        field("team", STR, "Batter's team."),
        field("team_id", I64, "Numeric ESPN team identifier."),
        field("batting_position", I32, "Batting position."),
        field("player", STR, "Batter's scorecard name.", nullable=False),
        field(
            "player_id", I64, "Numeric ESPN athlete identifier; joins to people.key_cricinfo.", nullable=False
        ),
        field("runs", I32, "Runs scored."),
        field("balls_faced", I32, "Balls faced."),
        field("minutes", I32, "Minutes batted."),
        field("fours", I32, "Fours."),
        field("sixes", I32, "Sixes."),
        field("strike_rate", RATE, "Runs per 100 balls."),
        field("is_out", BOOL, "Whether the batter was dismissed."),
        field("dismissal", STR, "Dismissal text such as caught, lbw, or not out."),
        field("is_batting", BOOL, "Whether the batter is at the crease right now."),
    ]
)

LIVE_BOWLING_SCHEMA = pa.schema(
    [
        field("event_id", I64, "Numeric ESPN event identifier.", nullable=False),
        field("innings_number", I32, "Innings order within the match.", nullable=False),
        field("team", STR, "Bowler's team."),
        field("team_id", I64, "Numeric ESPN team identifier."),
        field("player", STR, "Bowler's scorecard name.", nullable=False),
        field(
            "player_id", I64, "Numeric ESPN athlete identifier; joins to people.key_cricinfo.", nullable=False
        ),
        field("overs", STR, "Overs bowled in cricket notation."),
        field("balls", I32, "Legal balls bowled."),
        field("maidens", I32, "Maidens."),
        field("runs_conceded", I32, "Runs conceded."),
        field("wickets", I32, "Wickets."),
        field("economy", RATE, "Runs conceded per over."),
        field("dots", I32, "Dot balls."),
        field("fours_conceded", I32, "Fours conceded."),
        field("sixes_conceded", I32, "Sixes conceded."),
        field("wides", I32, "Wides."),
        field("no_balls", I32, "No-balls."),
    ]
)

COMMENTARY_SCHEMA = pa.schema(
    [
        field("event_id", I64, "Numeric ESPN event identifier.", nullable=False),
        field("sequence", I64, "ESPN ordering key for the ball.", nullable=False),
        field("innings_number", I32, "Innings number."),
        field("over_number", I32, "Over number, starting at 1."),
        field("ball_in_over", I32, "Ball within the over."),
        field("overs", STR, "Over.ball notation, for example 7.6."),
        field("batting_team", STR, "Batting team."),
        field("bowler", STR, "Bowler."),
        field("bowler_id", I64, "ESPN athlete id of the bowler."),
        field("batter", STR, "Batter on strike."),
        field("batter_id", I64, "ESPN athlete id of the batter."),
        field("play_type", STR, "ESPN play type such as run, no run, four, six, wide, or out."),
        field("runs", I32, "Runs scored from the ball."),
        field("is_wicket", BOOL, "Whether a wicket fell."),
        field("innings_runs", I32, "Innings total after the ball."),
        field("innings_wickets", I32, "Innings wickets after the ball."),
        field("short_text", STR, "One-line description, for example Duffy to Sai Sudharsan, FOUR."),
        field("text", STR, "Full commentary text with markup removed."),
    ]
)

TEAM_RANKINGS_SCHEMA = pa.schema(
    [
        field("format", STR, "ICC ranking format: test, odi, t20, odiw, or t20w.", nullable=False),
        field("rank_date", DATE, "Date the ranking was published."),
        field("rank", I32, "Ranking position (1 = best)."),
        field("team", STR, "Team name.", nullable=False),
        field("team_abbreviation", STR, "Team short name."),
        field("team_id", I32, "ICC team identifier (opaque number)."),
        field("matches", I32, "Matches counted in the rating period."),
        field("points", I32, "Rating points (sum over the rating period)."),
        field("rating", I32, "Rating in ICC points: weighted points divided by matches."),
        field("rank_change", STR, "Movement since the previous update, for example (+2) or ( - )."),
    ]
)

PLAYER_RANKINGS_SCHEMA = pa.schema(
    [
        field("format", STR, "ICC ranking format: test, odi, t20, odiw, or t20w.", nullable=False),
        field("discipline", STR, "bat, bowl, or allrounder.", nullable=False),
        field("rank_date", DATE, "Date the ranking was published."),
        field("rank", I32, "Ranking position (1 = best); tied players share a rank."),
        field("player", STR, "Player name."),
        field("player_id", I64, "ICC player identifier (opaque number).", nullable=False),
        field("country", STR, "Country name."),
        field("country_abbreviation", STR, "Country short name."),
        field("team_id", I32, "ICC team identifier (opaque number)."),
        field("rating", I32, "Rating in ICC points (0 to about 1000)."),
        field("career_best", STR, "Career-best rating and occasion."),
        field("rank_change", STR, "Movement since the previous update."),
    ]
)

ECB_TOURNAMENTS_SCHEMA = pa.schema(
    [
        field("tournament_id", I64, "ECB PulseLive tournament identifier (opaque number).", nullable=False),
        field("name", STR, "Tournament name."),
        field(
            "tournament_type",
            STR,
            "ECB tournament type, for example ENGLISH_DOMESTIC, INTERNATIONAL, or WOMEN_INTERNATIONAL.",
        ),
        field("match_types", STR, "Scheduled match counts by type, for example T20:34."),
        field("total_matches", I32, "Count of scheduled matches across all match types."),
        field("provisional", BOOL, "Whether ECB flags the tournament as provisional."),
    ]
)

ECB_FIXTURES_SCHEMA = pa.schema(
    [
        field("fixture_id", I64, "Numeric ECB PulseLive fixture identifier.", nullable=False),
        field("tournament_id", I64, "Numeric ECB tournament identifier."),
        field("tournament_name", STR, "Tournament name."),
        field("label", STR, "Fixture label, for example TR v MSG, The Hundred Men, 16 Aug."),
        field("description", STR, "Fixture description such as Final."),
        field("start_time", TIMESTAMP, "Scheduled start time in UTC."),
        field("end_time", TIMESTAMP, "Finish time in UTC, when recorded."),
        field("match_type", STR, "Match type such as T20, ODI, or FIRST_CLASS."),
        field("match_state", STR, "U upcoming, L live, or C complete."),
        field("venue", STR, "Ground name."),
        field("venue_city", STR, "Ground city or locality."),
        field("team1", STR, "First team."),
        field("team1_id", I64, "First team's ECB id."),
        field("team1_score", STR, "First team's innings scores, for example 158/8 (20.0)."),
        field("team2", STR, "Second team."),
        field("team2_id", I64, "Second team's ECB id."),
        field("team2_score", STR, "Second team's innings scores."),
        field("result", STR, "Result text, for example Manchester Super Giants Men win by 5 wickets."),
        field("overs_limit", I32, "Scheduled overs per innings, when limited."),
    ]
)

ECB_STANDINGS_SCHEMA = pa.schema(
    [
        field("tournament_id", I64, "Numeric ECB tournament identifier.", nullable=False),
        field("tournament_name", STR, "Tournament name."),
        field("group_name", STR, "Group or division name."),
        field("position", I32, "Official table position."),
        field("team", STR, "Team name.", nullable=False),
        field("team_abbreviation", STR, "Team abbreviation."),
        field("team_id", I64, "Numeric ECB team identifier.", nullable=False),
        field("played", I32, "Matches played."),
        field("won", I32, "Wins."),
        field("lost", I32, "Losses."),
        field(
            "tied",
            I32,
            "Ties; recomputed from played minus other results when ECB repeats the draw count here.",
        ),
        field("drawn", I32, "Draws."),
        field("no_result", I32, "No results."),
        field("batting_bonus", I32, "Batting bonus points (County Championship)."),
        field("bowling_bonus", I32, "Bowling bonus points (County Championship)."),
        field("deductions", POINTS, "Points deducted."),
        field("points", POINTS, "Official points total."),
        field("net_run_rate", NRR, "Official net run rate; NULL for competitions that do not use it."),
        field("runs_for", I32, "Runs scored."),
        field("balls_for", I32, "Balls faced."),
        field("runs_against", I32, "Runs conceded."),
        field("balls_against", I32, "Balls bowled."),
        field("recent_form", STR, "Recent results, most recent first, for example LLWWW."),
    ]
)
