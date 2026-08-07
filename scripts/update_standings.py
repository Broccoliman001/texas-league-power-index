import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

SPORT_ID = 12
LEAGUE_ID = 109

STANDINGS_URL = (
    "https://statsapi.mlb.com/api/v1/standings"
    "?sportId={sport_id}"
    "&leagueId={league_id}"
    "&season={season}"
    "&standingsTypes={standings_type}"
    "&hydrate=division"
)

SCHEDULE_URL = (
    "https://statsapi.mlb.com/api/v1/schedule"
    "?sportId={sport_id}"
    "&leagueId={league_id}"
    "&startDate={date}"
    "&endDate={date}"
    "&hydrate=team,linescore"
)

SCHEDULE_RANGE_URL = (
    "https://statsapi.mlb.com/api/v1/schedule"
    "?sportId={sport_id}"
    "&leagueId={league_id}"
    "&startDate={start_date}"
    "&endDate={end_date}"
    "&hydrate=team,linescore"
)

OUTPUT_PATH = Path("data/standings.json")
HISTORY_DIR = Path("data/history")

POWER_SMOOTHING_PREVIOUS_WEIGHT = 0.75
POWER_SMOOTHING_RAW_WEIGHT = 0.25

POWER_COMPRESSION_CENTER = 50
POWER_COMPRESSION_FACTOR = 0.75

OWP_LOWER_BOUND = 0.450
OWP_UPPER_BOUND = 0.550

TEAM_NAMES = {
    "Travelers": "Arkansas Travelers",
    "RoughRiders": "Frisco RoughRiders",
    "Drillers": "Tulsa Drillers",
    "RockHounds": "Midland RockHounds",
    "Wind Surge": "Wichita Wind Surge",
    "Hooks": "Corpus Christi Hooks",
    "Naturals": "Northwest Arkansas Naturals",
    "Sod Poodles": "Amarillo Sod Poodles",
    "Cardinals": "Springfield Cardinals",
    "Missions": "San Antonio Missions",
}


# The standings endpoint normally returns hydrated division names because the
# request includes hydrate=division. These IDs and team assignments are
# defensive fallbacks for API responses that contain only division IDs or omit
# division metadata, as occurred in the August 2026 response.
DIVISION_ID_KEYS = {
    241: "North",
    242: "South",
}

TEAM_DIVISIONS_BY_ID = {
    574: "North",   # Arkansas Travelers
    1350: "North",  # Northwest Arkansas Naturals
    440: "North",   # Springfield Cardinals
    260: "North",   # Tulsa Drillers
    3898: "North",  # Wichita Wind Surge
    5368: "South",  # Amarillo Sod Poodles
    482: "South",   # Corpus Christi Hooks
    540: "South",   # Frisco RoughRiders
    237: "South",   # Midland RockHounds
    510: "South",   # San Antonio Missions
}

EXPECTED_DIVISIONS = {"North", "South"}


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------


def normalize_team_name(name):
    return TEAM_NAMES.get(name, name)


def format_record(record):
    if not record:
        return "0-0"

    return f"{record.get('wins', 0)}-{record.get('losses', 0)}"


def format_pct(value):
    return f"{value:.3f}".replace("0.", ".")


def safe_int(value, default=999):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_division_key(
    division_name=None,
    division_id=None,
    team_id=None,
):
    normalized_name = (division_name or "").strip().lower()

    if "north" in normalized_name:
        return "North"

    if "south" in normalized_name:
        return "South"

    if division_id in DIVISION_ID_KEYS:
        return DIVISION_ID_KEYS[division_id]

    if team_id in TEAM_DIVISIONS_BY_ID:
        return TEAM_DIVISIONS_BY_ID[team_id]

    return "Unknown"


def get_division_display_name(division_key, division_name=None):
    if division_name:
        return division_name

    if division_key in EXPECTED_DIVISIONS:
        return f"Texas League {division_key}"

    return ""


def get_record_values(team_data):
    if not team_data:
        return {
            "wins": 0,
            "losses": 0,
            "games": 0,
            "pct_num": 0.0,
            "pct": ".000",
            "record": "0-0",
        }

    wins = team_data.get("wins", 0)
    losses = team_data.get("losses", 0)
    games = wins + losses
    pct_num = wins / games if games else 0.0

    pct = team_data.get("winningPercentage")

    if pct is None:
        pct = format_pct(pct_num)

    return {
        "wins": wins,
        "losses": losses,
        "games": games,
        "pct_num": pct_num,
        "pct": pct,
        "record": f"{wins}-{losses}",
    }


def format_games_back(value):
    if value is None or abs(value) < 0.0001:
        return "-"

    return f"{value:.1f}"


def has_official_clinch_marker(team_data):
    if not team_data:
        return False

    clinched_value = team_data.get("clinched")

    if clinched_value is True:
        return True

    if isinstance(clinched_value, str):
        if clinched_value.strip().lower() == "true":
            return True

    indicator = team_data.get("clinchIndicator")

    return indicator not in (None, "", "-")


def find_split_record(team_data, record_type):
    records = team_data.get("records", {})
    split_records = records.get("splitRecords", [])

    for record in split_records:
        if record.get("type") == record_type:
            return record

    return None


def find_expected_record(team_data):
    records = team_data.get("records", {})
    expected_records = records.get("expectedRecords", [])

    if expected_records:
        return expected_records[0]

    return None


# ---------------------------------------------------------------------------
# Standings collection and split-season state
# ---------------------------------------------------------------------------


def build_standings_url(season, standings_type):
    return STANDINGS_URL.format(
        sport_id=SPORT_ID,
        league_id=LEAGUE_ID,
        season=season,
        standings_type=standings_type,
    )


def fetch_standings(season, standings_type, required=False):
    url = build_standings_url(season, standings_type)

    print(f"\nFetching {standings_type} standings:")
    print(url)

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.json()

    except Exception as e:
        message = (
            f"Could not fetch {standings_type} standings "
            f"for {season}: {e}"
        )

        if required:
            raise RuntimeError(message) from e

        print(message)
        return {"records": []}


def count_standings_teams(data):
    return sum(
        len(record.get("teamRecords", []))
        for record in data.get("records", [])
    )


def resolve_season(today):
    candidate_seasons = [today.year]

    # During the offseason before a new schedule is populated, continue using
    # the most recent completed season rather than producing an empty page.
    if today.month <= 3:
        candidate_seasons.append(today.year - 1)

    last_data = None

    for season in candidate_seasons:
        data = fetch_standings(
            season,
            "regularSeason",
            required=False,
        )

        last_data = data

        if count_standings_teams(data) > 0:
            return season, data

    raise RuntimeError(
        "Could not locate regular-season Texas League standings for "
        f"candidate seasons {candidate_seasons}. Last response: {last_data}"
    )


def build_standings_lookup(data):
    lookup = {}

    for division_record in data.get("records", []):
        division_data = division_record.get("division", {})
        division_name = division_data.get("name", "")
        division_id = division_data.get("id")

        for team_data in division_record.get("teamRecords", []):
            team_id = team_data.get("team", {}).get("id")

            if team_id is None:
                continue

            division_key = get_division_key(
                division_name=division_name,
                division_id=division_id,
                team_id=team_id,
            )

            resolved_division_name = get_division_display_name(
                division_key,
                division_name,
            )

            values = get_record_values(team_data)

            lookup[team_id] = {
                "team_id": team_id,
                "team": normalize_team_name(
                    team_data.get("team", {}).get("name", "Unknown")
                ),
                "division": division_key,
                "division_name": resolved_division_name,
                "division_id": division_id,
                "wins": values["wins"],
                "losses": values["losses"],
                "games": values["games"],
                "pct_num": values["pct_num"],
                "pct": values["pct"],
                "record": values["record"],
                "games_back": team_data.get("gamesBack", "-"),
                "division_rank": team_data.get("divisionRank"),
                "division_rank_num": safe_int(
                    team_data.get("divisionRank")
                ),
                "league_rank": team_data.get("leagueRank"),
                "clinch_indicator": team_data.get("clinchIndicator"),
                "officially_clinched": has_official_clinch_marker(
                    team_data
                ),
                "raw": team_data,
            }

    return lookup


def total_team_games(lookup):
    return sum(team["games"] for team in lookup.values())


def records_match(first_lookup, second_lookup):
    if not first_lookup or not second_lookup:
        return False

    common_team_ids = set(first_lookup) & set(second_lookup)

    if len(common_team_ids) < 8:
        return False

    return all(
        first_lookup[team_id]["wins"]
        == second_lookup[team_id]["wins"]
        and first_lookup[team_id]["losses"]
        == second_lookup[team_id]["losses"]
        for team_id in common_team_ids
    )


def detect_active_half(
    current_half_lookup,
    first_half_lookup,
    second_half_lookup,
):
    second_half_has_games = total_team_games(second_half_lookup) > 0
    first_half_has_games = total_team_games(first_half_lookup) > 0

    if (
        second_half_has_games
        and records_match(current_half_lookup, second_half_lookup)
    ):
        return 2, "currentHalf matches secondHalf"

    if (
        first_half_has_games
        and records_match(current_half_lookup, first_half_lookup)
    ):
        return 1, "currentHalf matches firstHalf"

    # Fallback if the currentHalf request is delayed or unavailable.
    if second_half_has_games:
        return 2, "secondHalf contains completed games"

    if first_half_has_games:
        return 1, "firstHalf contains completed games"

    return None, "No completed half-season games were found"


def sort_standings_rows(rows):
    return sorted(
        rows,
        key=lambda team: (
            -team["pct_num"],
            team["division_rank_num"],
            -team["wins"],
            team["team"],
        ),
    )


def find_first_half_winners(first_half_lookup, active_half):
    winners = {}
    rows_by_division = {}

    for team in first_half_lookup.values():
        division_key = team.get("division", "Unknown")

        row = {
            "team_id": team["team_id"],
            "team": team["team"],
            "division": division_key,
            "division_name": team.get("division_name", ""),
            "division_id": team.get("division_id"),
            "wins": team["wins"],
            "losses": team["losses"],
            "record": team["record"],
            "pct": team["pct"],
            "pct_num": team["pct_num"],
            "division_rank": team.get("division_rank"),
            "division_rank_num": team.get(
                "division_rank_num",
                999,
            ),
            "clinch_indicator": team.get("clinch_indicator"),
            "officially_clinched": team.get(
                "officially_clinched",
                False,
            ),
        }

        rows_by_division.setdefault(
            division_key,
            [],
        ).append(row)

    for division_key, team_rows in rows_by_division.items():
        if division_key == "Unknown" or not team_rows:
            continue

        ranked_rows = sort_standings_rows(team_rows)
        official_rows = [
            row for row in ranked_rows
            if row["officially_clinched"]
        ]

        winner = None
        source = None

        if official_rows:
            # First-half standings use the marker to identify the team that
            # secured the first-half berth.
            winner = official_rows[0]
            source = "official_first_half_clinch_indicator"

        elif active_half == 2:
            # Once the second half is active, the first-half standings are
            # final. The division's first-ranked club owns the first berth.
            winner = ranked_rows[0]
            source = "final_first_half_rank"

        if winner:
            winner = dict(winner)
            winner["berth_type"] = "FIRST_HALF_CHAMPION"
            winner["source"] = source
            winners[division_key] = winner

    return winners


def build_eligible_race_rows(rows, excluded_team_id=None):
    eligible_rows = [
        dict(row)
        for row in rows
        if row["team_id"] != excluded_team_id
    ]

    eligible_rows = sort_standings_rows(eligible_rows)

    if not eligible_rows:
        return []

    leader = eligible_rows[0]

    for index, row in enumerate(eligible_rows, start=1):
        games_back_num = (
            (leader["wins"] - row["wins"])
            + (row["losses"] - leader["losses"])
        ) / 2

        row["eligible_rank"] = index
        row["eligible_games_back_num"] = games_back_num
        row["eligible_games_back"] = format_games_back(
            games_back_num
        )

        row.pop("raw", None)

    return eligible_rows


def build_playoff_races(
    regular_season_lookup,
    first_half_lookup,
    second_half_lookup,
    current_half_lookup,
    first_half_winners,
    active_half,
):
    races = {}
    division_team_ids = {}

    for team_id, team in regular_season_lookup.items():
        division_key = team.get("division", "Unknown")

        division_team_ids.setdefault(
            division_key,
            set(),
        ).add(team_id)

    for division_key, team_ids in division_team_ids.items():
        if division_key == "Unknown":
            continue

        representative_team = next(
            (
                regular_season_lookup[team_id]
                for team_id in team_ids
                if team_id in regular_season_lookup
            ),
            {},
        )

        division_name = representative_team.get(
            "division_name",
            get_division_display_name(division_key),
        )

        first_half_winner = first_half_winners.get(division_key)

        if active_half == 2:
            standings_type = "secondHalf"
            race_type = "SECOND_HALF_BERTH"
            source_lookup = second_half_lookup
            excluded_team_id = (
                first_half_winner.get("team_id")
                if first_half_winner else None
            )

        elif active_half == 1:
            standings_type = "firstHalf"
            race_type = "FIRST_HALF_BERTH"
            source_lookup = first_half_lookup
            excluded_team_id = None

        else:
            standings_type = "currentHalf"
            race_type = "UNKNOWN"
            source_lookup = current_half_lookup
            excluded_team_id = None

        division_rows = [
            team
            for team_id, team in source_lookup.items()
            if team_id in team_ids
        ]

        first_half_complete_waiting_for_second = (
            active_half == 1
            and first_half_winner is not None
        )

        if first_half_complete_waiting_for_second:
            eligible_rows = []
            race_status = "FIRST_HALF_CLINCHED"

        else:
            eligible_rows = build_eligible_race_rows(
                division_rows,
                excluded_team_id=excluded_team_id,
            )

            eligible_clinched_rows = [
                row
                for row in eligible_rows
                if row.get("officially_clinched")
            ]

            if (
                active_half == 2
                and eligible_clinched_rows
            ):
                # A marker on an eligible team can represent the secured
                # second-half berth. The first-half champion has already
                # been excluded, preventing its persistent asterisk from
                # being mistaken for a second-half clinch.
                race_status = "SECOND_HALF_CLINCHED"
            else:
                race_status = (
                    "ACTIVE"
                    if eligible_rows else "DATA_UNAVAILABLE"
                )

        open_berth_leader = (
            eligible_rows[0]
            if eligible_rows else None
        )

        races[division_key] = {
            "division": division_key,
            "division_name": division_name,
            "active_half": active_half,
            "race_type": race_type,
            "race_status": race_status,
            "standings_type": standings_type,
            "clinched_team": first_half_winner,
            "open_berth_basis": (
                "second_half_winning_percentage"
                if active_half == 2
                else "first_half_winning_percentage"
                if active_half == 1
                else "unknown"
            ),
            "open_berth_leader": open_berth_leader,
            "eligible_teams": eligible_rows,
        }

    return races


def build_team_playoff_state(playoff_races):
    state_by_team_id = {}

    for race in playoff_races.values():
        clinched_team = race.get("clinched_team")

        if clinched_team:
            state_by_team_id[clinched_team["team_id"]] = {
                "playoff_clinched": True,
                "berth_type": "FIRST_HALF_CHAMPION",
                "active_race_eligible": False,
                "active_race_rank": None,
                "active_race_games_back": None,
            }

        second_half_clinched = (
            race.get("race_status") == "SECOND_HALF_CLINCHED"
        )
        open_berth_leader = race.get("open_berth_leader")

        for row in race.get("eligible_teams", []):
            row_has_second_half_berth = (
                second_half_clinched
                and open_berth_leader is not None
                and row["team_id"] == open_berth_leader["team_id"]
            )

            state_by_team_id[row["team_id"]] = {
                "playoff_clinched": row_has_second_half_berth,
                "berth_type": (
                    "SECOND_HALF_REPRESENTATIVE"
                    if row_has_second_half_berth
                    else "NONE"
                ),
                "active_race_eligible": True,
                "active_race_rank": row.get("eligible_rank"),
                "active_race_games_back": row.get(
                    "eligible_games_back"
                ),
            }

    return state_by_team_id


# ---------------------------------------------------------------------------
# Existing snapshot and Power Index helpers
# ---------------------------------------------------------------------------


def load_rank_snapshot(path):
    if not path or not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            team["team"]: team["rank"]
            for team in data.get("teams", [])
            if "team" in team and "rank" in team
        }

    except Exception as e:
        print(f"Could not load rank snapshot from {path}: {e}")
        return {}


def load_power_snapshot(path):
    if not path or not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            team["team"]: team["power_score"]
            for team in data.get("teams", [])
            if "team" in team and "power_score" in team
        }

    except Exception as e:
        print(f"Could not load power snapshot from {path}: {e}")
        return {}


def find_comparison_snapshot(today):
    monday = today - timedelta(days=today.weekday())

    monday_path = HISTORY_DIR / f"{monday.isoformat()}.json"

    if monday_path.exists():
        return monday_path

    for offset in range(1, 7):
        candidate_date = monday + timedelta(days=offset)
        candidate_path = HISTORY_DIR / f"{candidate_date.isoformat()}.json"

        if candidate_path.exists():
            return candidate_path

    return None


def normalize(value, values, reverse=False):
    min_value = min(values)
    max_value = max(values)

    if max_value == min_value:
        return 50

    score = 100 * (value - min_value) / (max_value - min_value)

    return 100 - score if reverse else score


def normalize_bounded(value, lower_bound, upper_bound, reverse=False):
    if upper_bound == lower_bound:
        return 50

    score = 100 * (value - lower_bound) / (upper_bound - lower_bound)

    score = max(0, min(100, score))

    return 100 - score if reverse else score


def format_power_delta(delta):
    rounded_delta = round(delta, 1)

    if rounded_delta > 0:
        return f"+{rounded_delta:.1f}"

    return f"{rounded_delta:.1f}"


def get_power_delta_direction(delta):
    rounded_delta = round(delta, 1)

    if rounded_delta > 0:
        return "up"

    if rounded_delta < 0:
        return "down"

    return "neutral"


# ---------------------------------------------------------------------------
# Schedule and OWP helpers
# ---------------------------------------------------------------------------


def get_previous_games(
    texas_league_team_ids,
    max_games=5,
    max_days_back=10,
):
    previous_games = []
    seen_game_pks = set()

    start_date = datetime.now(timezone.utc).date() - timedelta(days=1)

    for offset in range(max_days_back):
        game_date = start_date - timedelta(days=offset)

        url = SCHEDULE_URL.format(
            sport_id=SPORT_ID,
            league_id=LEAGUE_ID,
            date=game_date.isoformat(),
        )

        print(f"\nChecking schedule for {game_date}")
        print(url)

        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            data = response.json()

        except Exception as e:
            print(f"Could not fetch schedule for {game_date}: {e}")
            continue

        for date_block in data.get("dates", []):
            for game in date_block.get("games", []):
                away_debug = (
                    game.get("teams", {})
                    .get("away", {})
                    .get("team", {})
                )
                home_debug = (
                    game.get("teams", {})
                    .get("home", {})
                    .get("team", {})
                )

                print(
                    game.get("gameDate"),
                    away_debug.get("id"),
                    away_debug.get("name"),
                    "at",
                    home_debug.get("id"),
                    home_debug.get("name"),
                    game.get("status", {}).get("abstractGameState"),
                    game.get("status", {}).get("detailedState"),
                )

                game_pk = game.get("gamePk")

                if game_pk in seen_game_pks:
                    continue

                status = game.get("status", {})
                abstract_state = status.get("abstractGameState", "")
                detailed_state = status.get("detailedState", "")

                if abstract_state != "Final":
                    continue

                away = game.get("teams", {}).get("away", {})
                home = game.get("teams", {}).get("home", {})

                away_team_data = away.get("team", {})
                home_team_data = home.get("team", {})

                away_team_id = away_team_data.get("id")
                home_team_id = home_team_data.get("id")

                if (
                    away_team_id not in texas_league_team_ids
                    and home_team_id not in texas_league_team_ids
                ):
                    continue

                away_team = normalize_team_name(
                    away_team_data.get("name", "Away")
                )

                home_team = normalize_team_name(
                    home_team_data.get("name", "Home")
                )

                previous_games.append({
                    "game_date": game_date.isoformat(),
                    "status": detailed_state,
                    "away_team": away_team,
                    "away_score": away.get("score", 0),
                    "home_team": home_team,
                    "home_score": home.get("score", 0),
                })

                seen_game_pks.add(game_pk)

                if len(previous_games) >= max_games:
                    return previous_games

    return previous_games


def get_completed_games_for_owp(
    texas_league_team_ids,
    start_date,
    end_date,
):
    completed_games = []
    seen_game_pks = set()

    url = SCHEDULE_RANGE_URL.format(
        sport_id=SPORT_ID,
        league_id=LEAGUE_ID,
        start_date=start_date,
        end_date=end_date,
    )

    print("\nFetching completed schedule for OWP:")
    print(url)

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        print(f"Could not fetch schedule range for OWP: {e}")
        return completed_games

    for date_block in data.get("dates", []):
        game_date = date_block.get("date")

        for game in date_block.get("games", []):
            game_pk = game.get("gamePk")

            if game_pk in seen_game_pks:
                continue

            status = game.get("status", {})
            abstract_state = status.get("abstractGameState", "")

            if abstract_state != "Final":
                continue

            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})

            away_team_data = away.get("team", {})
            home_team_data = home.get("team", {})

            away_team_id = away_team_data.get("id")
            home_team_id = home_team_data.get("id")

            if (
                away_team_id not in texas_league_team_ids
                or home_team_id not in texas_league_team_ids
            ):
                continue

            completed_games.append({
                "game_pk": game_pk,
                "game_date": game_date,
                "away_team_id": away_team_id,
                "home_team_id": home_team_id,
            })

            seen_game_pks.add(game_pk)

    print(f"Completed games used for OWP: {len(completed_games)}")

    return completed_games


def calculate_opponent_win_percentages(
    completed_games,
    team_win_pct_by_id,
):
    opponent_win_pcts = {
        team_id: []
        for team_id in team_win_pct_by_id
    }

    for game in completed_games:
        away_team_id = game["away_team_id"]
        home_team_id = game["home_team_id"]

        away_win_pct = team_win_pct_by_id.get(away_team_id)
        home_win_pct = team_win_pct_by_id.get(home_team_id)

        if away_win_pct is None or home_win_pct is None:
            continue

        opponent_win_pcts[away_team_id].append(home_win_pct)
        opponent_win_pcts[home_team_id].append(away_win_pct)

    average_opponent_win_pct = {}

    for team_id, opponent_values in opponent_win_pcts.items():
        if opponent_values:
            average_opponent_win_pct[team_id] = (
                sum(opponent_values) / len(opponent_values)
            )
        else:
            average_opponent_win_pct[team_id] = 0.500

    return average_opponent_win_pct


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------


today = datetime.now(timezone.utc).date()

season, regular_season_data = resolve_season(today)
season_start_date = f"{season}-01-01"

first_half_data = fetch_standings(
    season,
    "firstHalf",
    required=False,
)
second_half_data = fetch_standings(
    season,
    "secondHalf",
    required=False,
)
current_half_data = fetch_standings(
    season,
    "currentHalf",
    required=False,
)

regular_season_lookup = build_standings_lookup(
    regular_season_data
)
first_half_lookup = build_standings_lookup(first_half_data)
second_half_lookup = build_standings_lookup(second_half_data)
current_half_lookup = build_standings_lookup(current_half_data)

active_half, active_half_detection_method = detect_active_half(
    current_half_lookup,
    first_half_lookup,
    second_half_lookup,
)

first_half_winners = find_first_half_winners(
    first_half_lookup,
    active_half,
)

playoff_races = build_playoff_races(
    regular_season_lookup,
    first_half_lookup,
    second_half_lookup,
    current_half_lookup,
    first_half_winners,
    active_half,
)

team_playoff_state = build_team_playoff_state(playoff_races)

comparison_snapshot_path = find_comparison_snapshot(today)

previous_ranks = (
    load_rank_snapshot(comparison_snapshot_path)
    if comparison_snapshot_path else {}
)

comparison_powers = (
    load_power_snapshot(comparison_snapshot_path)
    if comparison_snapshot_path else {}
)

previous_powers = load_power_snapshot(OUTPUT_PATH)

if comparison_snapshot_path:
    print(
        "Comparing trends and power deltas against weekly baseline "
        f"{comparison_snapshot_path}"
    )
else:
    print(
        "No weekly baseline snapshot found. "
        "Trends and power deltas will default to neutral."
    )

texas_league_team_ids = set()
team_win_pct_by_id = {}

for division in regular_season_data.get("records", []):
    for team_data in division.get("teamRecords", []):
        team_id = team_data["team"]["id"]

        wins = team_data["wins"]
        losses = team_data["losses"]
        games = wins + losses

        texas_league_team_ids.add(team_id)
        team_win_pct_by_id[team_id] = (
            wins / games if games else 0.500
        )

print("\nTexas League team IDs:")
print(sorted(texas_league_team_ids))

completed_games = get_completed_games_for_owp(
    texas_league_team_ids,
    season_start_date,
    today.isoformat(),
)

average_opponent_win_pct_by_id = (
    calculate_opponent_win_percentages(
        completed_games,
        team_win_pct_by_id,
    )
)

previous_games = get_previous_games(texas_league_team_ids)

teams = []

for division in regular_season_data.get("records", []):
    for team_data in division.get("teamRecords", []):
        team_id = team_data["team"]["id"]

        regular_record = regular_season_lookup.get(team_id, {})
        division_key = regular_record.get("division", "Unknown")
        division_name = regular_record.get("division_name", "")

        wins = team_data["wins"]
        losses = team_data["losses"]

        games = wins + losses

        rs = team_data.get("runsScored", 0)
        ra = team_data.get("runsAllowed", 0)

        diff = rs - ra

        raw_team_name = team_data["team"]["name"]
        display_team_name = normalize_team_name(raw_team_name)

        expected_record = find_expected_record(team_data)
        xwl = format_record(expected_record)

        last10_record = find_split_record(team_data, "lastTen")
        vs500_record = find_split_record(team_data, "winners")

        opponent_win_pct_num = average_opponent_win_pct_by_id.get(
            team_id,
            0.500,
        )

        first_half_record = first_half_lookup.get(team_id)
        second_half_record = second_half_lookup.get(team_id)
        current_half_record = current_half_lookup.get(team_id)

        first_half_values = get_record_values(
            first_half_record["raw"]
            if first_half_record else None
        )
        second_half_values = get_record_values(
            second_half_record["raw"]
            if second_half_record else None
        )
        current_half_values = get_record_values(
            current_half_record["raw"]
            if current_half_record else None
        )

        playoff_state = team_playoff_state.get(
            team_id,
            {
                "playoff_clinched": False,
                "berth_type": "NONE",
                "active_race_eligible": False,
                "active_race_rank": None,
                "active_race_games_back": None,
            },
        )

        first_half_winner = (
            first_half_winners.get(division_key, {}).get(
                "team_id"
            ) == team_id
        )

        # The asterisk carried in secondHalf/currentHalf standings identifies
        # a previously secured first-half berth. It must not be interpreted as
        # proof that a team has clinched the second-half berth.
        playoff_clinched = playoff_state["playoff_clinched"]
        berth_type = playoff_state["berth_type"]

        team = {
            "team_id": team_id,
            "team": display_team_name,
            "division": division_key,
            "division_name": division_name,

            # Existing overall-season fields used by the Power Index and page.
            "record": f"{wins}-{losses}",
            "pct": team_data.get(
                "winningPercentage",
                f"{wins / games:.3f}" if games else ".000",
            ),
            "rs": rs,
            "ra": ra,
            "xwl": xwl,
            "last10": format_record(last10_record),
            "vs500": format_record(vs500_record),
            "opponent_win_pct": format_pct(opponent_win_pct_num),
            "owp": format_pct(opponent_win_pct_num),
            "identity": "TBD",
            "wins": wins,
            "losses": losses,
            "diff": diff,
            "win_pct_num": wins / games if games else 0,
            "opponent_win_pct_num": opponent_win_pct_num,
            "owp_num": opponent_win_pct_num,
            "diff_per_game": diff / games if games else 0,

            # Split-season standings fields.
            "first_half_record": first_half_values["record"],
            "first_half_wins": first_half_values["wins"],
            "first_half_losses": first_half_values["losses"],
            "first_half_pct": first_half_values["pct"],
            "first_half_pct_num": first_half_values["pct_num"],
            "first_half_division_rank": (
                first_half_record.get("division_rank")
                if first_half_record else None
            ),
            "first_half_clinch_indicator": (
                first_half_record.get("clinch_indicator")
                if first_half_record else None
            ),
            "first_half_winner": first_half_winner,

            "second_half_record": second_half_values["record"],
            "second_half_wins": second_half_values["wins"],
            "second_half_losses": second_half_values["losses"],
            "second_half_pct": second_half_values["pct"],
            "second_half_pct_num": second_half_values["pct_num"],
            "second_half_division_rank": (
                second_half_record.get("division_rank")
                if second_half_record else None
            ),
            "second_half_games_back": (
                second_half_record.get("games_back")
                if second_half_record else None
            ),
            "second_half_clinch_indicator": (
                second_half_record.get("clinch_indicator")
                if second_half_record else None
            ),

            "current_half_record": current_half_values["record"],
            "current_half_wins": current_half_values["wins"],
            "current_half_losses": current_half_values["losses"],
            "current_half_pct": current_half_values["pct"],
            "current_half_pct_num": current_half_values["pct_num"],

            # Playoff-state fields for simple front-end rendering.
            "active_half": active_half,
            "playoff_clinched": playoff_clinched,
            "berth_type": berth_type,
            "active_playoff_race_eligible": playoff_state[
                "active_race_eligible"
            ],
            "active_playoff_race_rank": playoff_state[
                "active_race_rank"
            ],
            "active_playoff_race_games_back": playoff_state[
                "active_race_games_back"
            ],
        }

        teams.append(team)

if not teams:
    raise RuntimeError(
        "No Texas League teams were found in regular-season standings."
    )

diff_values = [team["diff_per_game"] for team in teams]
actual_win_values = [team["win_pct_num"] for team in teams]

for team in teams:
    diff_score = normalize(
        team["diff_per_game"],
        diff_values,
    )

    actual_win_score = normalize(
        team["win_pct_num"],
        actual_win_values,
    )

    opponent_strength_score = normalize_bounded(
        team["opponent_win_pct_num"],
        OWP_LOWER_BOUND,
        OWP_UPPER_BOUND,
    )

    raw_power_score = (
        0.50 * diff_score
        + 0.25 * actual_win_score
        + 0.25 * opponent_strength_score
    )

    previous_power_score = previous_powers.get(
        team["team"],
        raw_power_score,
    )

    displayed_power_score = (
        previous_power_score
        * POWER_SMOOTHING_PREVIOUS_WEIGHT
        + raw_power_score
        * POWER_SMOOTHING_RAW_WEIGHT
    )

    compressed_power_score = (
        POWER_COMPRESSION_CENTER
        + (
            (
                displayed_power_score
                - POWER_COMPRESSION_CENTER
            )
            * POWER_COMPRESSION_FACTOR
        )
    )

    comparison_power_score = comparison_powers.get(
        team["team"],
        compressed_power_score,
    )

    power_delta = (
        compressed_power_score
        - comparison_power_score
    )

    power_delta_direction = get_power_delta_direction(
        power_delta
    )

    team["diff_score"] = round(diff_score, 1)
    team["run_profile_score"] = round(diff_score, 1)
    team["actual_win_score"] = round(actual_win_score, 1)
    team["opponent_strength_score"] = round(
        opponent_strength_score,
        1,
    )
    team["owp_score"] = round(opponent_strength_score, 1)
    team["quality_record_score"] = round(
        opponent_strength_score,
        1,
    )
    team["raw_power_score"] = round(raw_power_score, 1)
    team["previous_power_score"] = round(
        previous_power_score,
        1,
    )
    team["displayed_power_score"] = round(
        displayed_power_score,
        1,
    )
    team["comparison_power_score"] = round(
        comparison_power_score,
        1,
    )
    team["power_score"] = round(compressed_power_score, 1)
    team["power_delta"] = round(power_delta, 1)
    team["power_delta_display"] = format_power_delta(
        power_delta
    )
    team["power_delta_direction"] = power_delta_direction
    team["power_delta_class"] = (
        f"delta-{power_delta_direction}"
    )

teams = sorted(
    teams,
    key=lambda team: team["power_score"],
    reverse=True,
)

for index, team in enumerate(teams, start=1):
    team["rank"] = index

    comparison_rank = previous_ranks.get(team["team"])

    if comparison_rank is None:
        team["trend"] = "→"

    elif index < comparison_rank:
        team["trend"] = "↑"

    elif index > comparison_rank:
        team["trend"] = "↓"

    else:
        team["trend"] = "→"

validation_warnings = []
expected_team_count = 10
regular_team_count = len(regular_season_lookup)
first_half_team_count = len(first_half_lookup)
second_half_team_count = len(second_half_lookup)
current_half_team_count = len(current_half_lookup)

detected_divisions = set(playoff_races)
first_half_winner_divisions = set(first_half_winners)
division_count = len(detected_divisions)
first_half_winner_count = len(first_half_winners)

unknown_division_teams = sorted(
    team["team"]
    for team in teams
    if team.get("division") == "Unknown"
)

if regular_team_count != expected_team_count:
    validation_warnings.append(
        "Expected 10 Texas League teams in regularSeason, "
        f"found {regular_team_count}."
    )

if unknown_division_teams:
    validation_warnings.append(
        "Could not resolve a division for: "
        + ", ".join(unknown_division_teams)
        + "."
    )

if detected_divisions != EXPECTED_DIVISIONS:
    validation_warnings.append(
        "Expected playoff races for North and South, but detected "
        f"{sorted(detected_divisions)}."
    )

if active_half is None:
    validation_warnings.append(
        "The active half could not be determined."
    )

if (
    active_half == 2
    and first_half_winner_divisions != EXPECTED_DIVISIONS
):
    validation_warnings.append(
        "Second half was detected, but the script did not identify "
        "the first-half champions for both North and South."
    )

if active_half == 1 and first_half_team_count == 0:
    validation_warnings.append(
        "First half was detected, but firstHalf standings are empty."
    )

if active_half == 2 and second_half_team_count == 0:
    validation_warnings.append(
        "Second half was detected, but secondHalf standings are empty."
    )

for division_key in EXPECTED_DIVISIONS:
    race = playoff_races.get(division_key)

    if not race:
        continue

    expected_division_team_count = 5
    actual_division_team_count = sum(
        1
        for team in teams
        if team.get("division") == division_key
    )

    if actual_division_team_count != expected_division_team_count:
        validation_warnings.append(
            f"Expected 5 teams in the {division_key} Division, "
            f"found {actual_division_team_count}."
        )

    if active_half == 2:
        eligible_team_count = len(race.get("eligible_teams", []))

        if eligible_team_count != 4:
            validation_warnings.append(
                f"Expected 4 eligible teams in the {division_key} "
                f"second-half race after excluding the first-half "
                f"champion, found {eligible_team_count}."
            )

playoff_state_valid = (
    regular_team_count == expected_team_count
    and active_half in (1, 2)
    and detected_divisions == EXPECTED_DIVISIONS
    and not unknown_division_teams
    and (
        active_half == 1
        or first_half_winner_divisions == EXPECTED_DIVISIONS
    )
    and not validation_warnings
)

output = {
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "season": season,
    "previous_games": previous_games,

    # Existing field retained for the current page.
    "data_source": "regularSeason",

    # New split-season metadata.
    "standings_sources": {
        "power_index": "regularSeason",
        "first_half": "firstHalf",
        "second_half": "secondHalf",
        "active_half": "currentHalf",
    },

    "season_state": {
        "split_season": True,
        "active_half": active_half,
        "active_half_label": (
            "First Half"
            if active_half == 1
            else "Second Half"
            if active_half == 2
            else "Unknown"
        ),
        "active_half_detection_method": (
            active_half_detection_method
        ),
        "first_half_complete": active_half == 2,
        "second_half_started": active_half == 2,
    },

    "playoff_format": {
        "berths_per_division": 2,
        "first_berth": "first_half_champion",
        "second_berth": (
            "highest second-half winning percentage excluding "
            "the first-half champion"
        ),
        "standings_measure": "winning_percentage",
    },

    "first_half_winners": first_half_winners,
    "playoff_races": playoff_races,

    "validation": {
        "playoff_state_valid": playoff_state_valid,
        "expected_team_count": expected_team_count,
        "regular_season_team_count": regular_team_count,
        "first_half_team_count": first_half_team_count,
        "second_half_team_count": second_half_team_count,
        "current_half_team_count": current_half_team_count,
        "division_count": division_count,
        "detected_divisions": sorted(detected_divisions),
        "expected_divisions": sorted(EXPECTED_DIVISIONS),
        "first_half_winner_count": first_half_winner_count,
        "first_half_winner_divisions": sorted(
            first_half_winner_divisions
        ),
        "unknown_division_teams": unknown_division_teams,
        "warnings": validation_warnings,
    },

    "trend_basis": (
        "Rank arrows compare against the weekly baseline "
        "snapshot from Monday or the first available "
        "snapshot of the current week."
    ),

    "power_delta_basis": (
        "Power delta compares against the same weekly "
        "baseline snapshot used for rank arrows."
    ),

    "comparison_snapshot": (
        str(comparison_snapshot_path)
        if comparison_snapshot_path else None
    ),

    "power_formula": {
        "formula": (
            "power_score = 50% Run Differential Per Game "
            "+ 25% Actual Winning Percentage "
            "+ 25% Average Opponent Winning Percentage"
        ),
        "diff": (
            "Run Differential Per Game = "
            "normalized run differential per game"
        ),
        "actual_winning_percentage": (
            "Actual Winning Percentage = "
            "normalized current winning percentage"
        ),
        "opponent_strength": (
            "Average Opponent Winning Percentage = "
            "bounded-normalized against .450 to .550"
        ),
    },

    "power_smoothing": {
        "enabled": True,
        "previous_weight": POWER_SMOOTHING_PREVIOUS_WEIGHT,
        "raw_weight": POWER_SMOOTHING_RAW_WEIGHT,
        "formula": (
            "displayed_power_score = previous_power_score "
            "* 0.75 + raw_power_score * 0.25"
        ),
    },

    "power_compression": {
        "enabled": True,
        "center": POWER_COMPRESSION_CENTER,
        "factor": POWER_COMPRESSION_FACTOR,
        "formula": (
            "power_score = 50 + "
            "((displayed_power_score - 50) * 0.75)"
        ),
    },

    "power_delta": {
        "enabled": True,
        "neutral_threshold": 0,
        "formula": (
            "power_delta = "
            "power_score - comparison_power_score"
        ),
    },

    "owp": {
        "enabled": True,
        "season_start_date": season_start_date,
        "lower_bound": OWP_LOWER_BOUND,
        "upper_bound": OWP_UPPER_BOUND,
        "formula": (
            "OWP = average current winning percentage of all "
            "opponents played, weighted by games played. "
            "OWP score is bounded-normalized so .450 = 0, "
            ".500 = 50, and .550 = 100."
        ),
        "completed_games_used": len(completed_games),
    },

    "teams": teams,
}

OUTPUT_PATH.parent.mkdir(exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

with OUTPUT_PATH.open("w", encoding="utf-8") as f:
    json.dump(output, f, indent=2)

history_path = HISTORY_DIR / f"{today.isoformat()}.json"

with history_path.open("w", encoding="utf-8") as f:
    json.dump(output, f, indent=2)

print(f"\nWrote {OUTPUT_PATH}")
print(f"Wrote {history_path}")
print(f"Season: {season}")
print(f"Active half: {active_half}")
print(f"Active-half detection: {active_half_detection_method}")
print(f"Detected divisions: {sorted(detected_divisions)}")
print(f"First-half winners found: {first_half_winner_count}")
print(
    "First-half winner divisions: "
    f"{sorted(first_half_winner_divisions)}"
)
print(f"Playoff state valid: {playoff_state_valid}")

if validation_warnings:
    print("Validation warnings:")

    for warning in validation_warnings:
        print(f"- {warning}")

print(f"Teams written: {len(teams)}")
print(f"Previous games written: {len(previous_games)}")
print(f"Completed games used for OWP: {len(completed_games)}")
print(f"Last updated: {output['last_updated']}")
