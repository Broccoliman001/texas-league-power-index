import hashlib
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

POWER_DIFF_WEIGHT = 0.50
POWER_WIN_PCT_WEIGHT = 0.25
POWER_OWP_WEIGHT = 0.25

RECENT_MAX_GAMES = 24
POWER_MODEL_VERSION = "2026-08-multiview-v2"
OWP_EXCLUDE_HEAD_TO_HEAD = True
RECENT_POWER_SMOOTHING_ENABLED = False

VIEW_CONFIG = {
    "overall": {
        "label": "Overall",
        "description": "All completed regular-season games.",
        "standings_type": "regularSeason",
    },
    "first_half": {
        "label": "First Half",
        "description": "Games assigned to the official first half.",
        "standings_type": "firstHalf",
    },
    "second_half": {
        "label": "Second Half",
        "description": "Games assigned to the official second half.",
        "standings_type": "secondHalf",
    },
    "recent": {
        "label": "Recent",
        "description": (
            "Each team\'s most recent up to 24 completed games."
        ),
        "standings_type": None,
    },
}

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
                "runs_scored": team_data.get("runsScored", 0),
                "runs_allowed": team_data.get("runsAllowed", 0),
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

# ---------------------------------------------------------------------------
# Snapshot, model-state, and Power Index helpers
# ---------------------------------------------------------------------------


def load_json_file(path):
    if not path or not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load JSON from {path}: {e}")
        return {}


def get_snapshot_view(snapshot, view_key):
    views = snapshot.get("views", {})
    view = views.get(view_key)

    if view:
        return view

    # Backward compatibility with snapshots created before multi-view support.
    if view_key == "overall" and snapshot.get("teams"):
        return {
            "teams": snapshot.get("teams", []),
            "input_fingerprint": None,
            "game_fingerprint": None,
        }

    return {}


def get_snapshot_team_map(snapshot, view_key):
    view = get_snapshot_view(snapshot, view_key)

    return {
        team["team"]: team
        for team in view.get("teams", [])
        if "team" in team
    }


def get_snapshot_view_fingerprint(snapshot, view_key):
    view = get_snapshot_view(snapshot, view_key)
    return view.get("input_fingerprint")


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


def compress_power_score(smoothed_power_score):
    return (
        POWER_COMPRESSION_CENTER
        + (
            (smoothed_power_score - POWER_COMPRESSION_CENTER)
            * POWER_COMPRESSION_FACTOR
        )
    )


def inverse_compress_power_score(power_score):
    if POWER_COMPRESSION_FACTOR == 0:
        return POWER_COMPRESSION_CENTER

    return (
        POWER_COMPRESSION_CENTER
        + (
            (power_score - POWER_COMPRESSION_CENTER)
            / POWER_COMPRESSION_FACTOR
        )
    )


def get_previous_smoothed_power(previous_team, fallback_raw_score):
    if not previous_team:
        return fallback_raw_score

    # New snapshots retain an unrounded internal smoothing state so repeated
    # runs with identical inputs never drift because of one-decimal storage.
    value = previous_team.get("smoothed_power_state")

    if value is not None:
        return value

    # Multiview snapshots created before the precision-state fix stored only
    # the one-decimal debugging value. Use it once as the migration fallback.
    value = previous_team.get("smoothed_power_score")

    if value is not None:
        return value

    # The pre-multiview script stored its uncompressed smoothing intermediate
    # as displayed_power_score. Prefer that for a gentler migration.
    value = previous_team.get("displayed_power_score")

    if value is not None:
        return value

    value = previous_team.get("power_score")

    if value is not None:
        return inverse_compress_power_score(value)

    return fallback_raw_score


def get_previous_display_power(previous_team, fallback_power_score):
    if not previous_team:
        return fallback_power_score

    value = previous_team.get("power_score")
    return fallback_power_score if value is None else value


def get_view_smoothing_config(view_key):
    if view_key == "recent" and not RECENT_POWER_SMOOTHING_ENABLED:
        return {
            "enabled": False,
            "previous_weight": 0.0,
            "raw_weight": 1.0,
        }

    return {
        "enabled": True,
        "previous_weight": POWER_SMOOTHING_PREVIOUS_WEIGHT,
        "raw_weight": POWER_SMOOTHING_RAW_WEIGHT,
    }


def power_model_signature(view_key):
    smoothing = get_view_smoothing_config(view_key)

    return {
        "model_version": POWER_MODEL_VERSION,
        "weights": {
            "diff": POWER_DIFF_WEIGHT,
            "win_pct": POWER_WIN_PCT_WEIGHT,
            "owp": POWER_OWP_WEIGHT,
        },
        "owp": {
            "lower_bound": OWP_LOWER_BOUND,
            "upper_bound": OWP_UPPER_BOUND,
            "exclude_head_to_head": OWP_EXCLUDE_HEAD_TO_HEAD,
            "empty_adjusted_record_fallback": 0.500,
        },
        "smoothing": smoothing,
        "compression": {
            "center": POWER_COMPRESSION_CENTER,
            "factor": POWER_COMPRESSION_FACTOR,
        },
    }


# ---------------------------------------------------------------------------
# Schedule, game-history, and view helpers
# ---------------------------------------------------------------------------


def game_sort_key(game):
    return (
        game.get("game_date") or "",
        game.get("game_datetime") or "",
        safe_int(game.get("game_number"), 1),
        safe_int(game.get("game_pk"), 0),
    )


def get_completed_games(
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

    print("\nFetching completed regular-season schedule:")
    print(url)

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        raise RuntimeError(
            f"Could not fetch Texas League schedule range: {e}"
        ) from e

    for date_block in data.get("dates", []):
        date_block_date = date_block.get("date")

        for game in date_block.get("games", []):
            game_pk = game.get("gamePk")

            if game_pk in seen_game_pks:
                continue

            status = game.get("status", {})
            abstract_state = status.get("abstractGameState", "")

            if abstract_state != "Final":
                continue

            # Keep postseason games out of the regular-season Power Index.
            # MLB StatsAPI uses R for regular-season games.
            game_type = game.get("gameType")

            if game_type not in (None, "R"):
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

            away_score = away.get("score")
            home_score = home.get("score")

            if away_score is None or home_score is None:
                print(
                    "Skipping final game without a complete score: "
                    f"gamePk={game_pk}"
                )
                continue

            completed_games.append({
                "game_pk": game_pk,
                "game_date": date_block_date,
                "game_datetime": game.get("gameDate"),
                "game_number": game.get("gameNumber", 1),
                "game_type": game_type,
                "status": status.get("detailedState", "Final"),
                "away_team_id": away_team_id,
                "away_team": normalize_team_name(
                    away_team_data.get("name", "Away")
                ),
                "away_score": away_score,
                "home_team_id": home_team_id,
                "home_team": normalize_team_name(
                    home_team_data.get("name", "Home")
                ),
                "home_score": home_score,
            })

            seen_game_pks.add(game_pk)

    completed_games.sort(key=game_sort_key)

    print(
        "Completed regular-season Texas League games fetched: "
        f"{len(completed_games)}"
    )

    return completed_games


def build_team_game_history(completed_games, texas_league_team_ids):
    history = {
        team_id: []
        for team_id in texas_league_team_ids
    }

    for game in completed_games:
        away_team_id = game["away_team_id"]
        home_team_id = game["home_team_id"]

        if away_team_id in history:
            history[away_team_id].append(game)

        if home_team_id in history:
            history[home_team_id].append(game)

    for team_id in history:
        history[team_id].sort(key=game_sort_key)

    return history


def calculate_team_game_stats(team_id, games):
    wins = 0
    losses = 0
    rs = 0
    ra = 0

    for game in games:
        if game["away_team_id"] == team_id:
            team_score = game["away_score"]
            opponent_score = game["home_score"]
        elif game["home_team_id"] == team_id:
            team_score = game["home_score"]
            opponent_score = game["away_score"]
        else:
            continue

        rs += team_score
        ra += opponent_score

        if team_score > opponent_score:
            wins += 1
        elif team_score < opponent_score:
            losses += 1

    games_with_decision = wins + losses
    pct_num = wins / games_with_decision if games_with_decision else 0.0

    return {
        "wins": wins,
        "losses": losses,
        "games": games_with_decision,
        "selected_games": len(games),
        "record": f"{wins}-{losses}",
        "pct_num": pct_num,
        "pct": format_pct(pct_num),
        "rs": rs,
        "ra": ra,
        "diff": rs - ra,
    }


def calculate_last_n_record(team_id, games, max_games=10):
    selected_games = games[-max_games:]
    stats = calculate_team_game_stats(team_id, selected_games)
    return stats["record"]


def select_first_half_games(
    all_games_by_team,
    first_half_lookup,
    active_half,
):
    selected = {}

    for team_id, games in all_games_by_team.items():
        official = first_half_lookup.get(team_id)

        if official:
            game_count = official.get("games", 0)
            selected[team_id] = list(games[:game_count])

        elif active_half == 1:
            # If the firstHalf endpoint is temporarily unavailable while the
            # first half is active, all completed games belong to that half.
            selected[team_id] = list(games)

        else:
            selected[team_id] = []

    return selected


def select_second_half_games(
    all_games_by_team,
    second_half_lookup,
):
    selected = {}

    for team_id, games in all_games_by_team.items():
        official = second_half_lookup.get(team_id)
        game_count = official.get("games", 0) if official else 0

        if game_count > 0:
            selected[team_id] = list(games[-game_count:])
        else:
            selected[team_id] = []

    return selected


def select_recent_games(all_games_by_team, max_games=RECENT_MAX_GAMES):
    return {
        team_id: list(games[-max_games:])
        for team_id, games in all_games_by_team.items()
    }


def build_view_game_sets(
    all_games_by_team,
    first_half_lookup,
    second_half_lookup,
    active_half,
):
    return {
        "overall": {
            team_id: list(games)
            for team_id, games in all_games_by_team.items()
        },
        "first_half": select_first_half_games(
            all_games_by_team,
            first_half_lookup,
            active_half,
        ),
        "second_half": select_second_half_games(
            all_games_by_team,
            second_half_lookup,
        ),
        "recent": select_recent_games(all_games_by_team),
    }


def opponent_id_for_game(team_id, game):
    if game["away_team_id"] == team_id:
        return game["home_team_id"]

    if game["home_team_id"] == team_id:
        return game["away_team_id"]

    return None


def calculate_head_to_head_excluded_win_pct(
    team_id,
    excluded_opponent_id,
    games,
):
    independent_games = [
        game
        for game in games
        if opponent_id_for_game(team_id, game) != excluded_opponent_id
    ]

    if not independent_games:
        # With no independent evidence yet (most relevant very early in the
        # season), use a neutral schedule-strength value rather than allowing
        # the evaluated team to create its own OWP signal.
        return 0.500

    stats = calculate_team_game_stats(team_id, independent_games)
    return stats["pct_num"]


def calculate_opponent_win_percentages_for_view(games_by_team):
    average_opponent_win_pct = {}
    adjusted_pct_cache = {}

    for team_id, games in games_by_team.items():
        opponent_values = []

        for game in games:
            opponent_id = opponent_id_for_game(team_id, game)

            if opponent_id is None:
                continue

            cache_key = (opponent_id, team_id)

            if cache_key not in adjusted_pct_cache:
                opponent_games = games_by_team.get(opponent_id, [])

                if OWP_EXCLUDE_HEAD_TO_HEAD:
                    adjusted_pct_cache[cache_key] = (
                        calculate_head_to_head_excluded_win_pct(
                            opponent_id,
                            team_id,
                            opponent_games,
                        )
                    )
                else:
                    stats = calculate_team_game_stats(
                        opponent_id,
                        opponent_games,
                    )
                    adjusted_pct_cache[cache_key] = (
                        stats["pct_num"] if stats["games"] else 0.500
                    )

            # Append once per game in the evaluated team's view so opponents
            # remain weighted by the number of times they were actually faced.
            opponent_values.append(adjusted_pct_cache[cache_key])

        if opponent_values:
            average_opponent_win_pct[team_id] = (
                sum(opponent_values) / len(opponent_values)
            )
        else:
            average_opponent_win_pct[team_id] = 0.500

    return average_opponent_win_pct


def build_previous_games(completed_games, max_games=5):
    previous_games = []

    for game in sorted(
        completed_games,
        key=game_sort_key,
        reverse=True,
    )[:max_games]:
        previous_games.append({
            "game_date": game.get("game_date"),
            "status": game.get("status", "Final"),
            "away_team": game.get("away_team", "Away"),
            "away_score": game.get("away_score", 0),
            "home_team": game.get("home_team", "Home"),
            "home_score": game.get("home_score", 0),
        })

    return previous_games


def unique_game_count(games_by_team):
    game_pks = {
        game["game_pk"]
        for games in games_by_team.values()
        for game in games
        if game.get("game_pk") is not None
    }

    return len(game_pks)


def build_game_fingerprint(view_key, games_by_team):
    payload = {
        "view": view_key,
        "games": {},
    }

    for team_id in sorted(games_by_team):
        payload["games"][str(team_id)] = [
            {
                "game_pk": game.get("game_pk"),
                "game_date": game.get("game_date"),
                "away_team_id": game.get("away_team_id"),
                "home_team_id": game.get("home_team_id"),
                "away_score": game.get("away_score"),
                "home_score": game.get("home_score"),
            }
            for game in games_by_team[team_id]
        ]

    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_input_fingerprint(view_key, teams, game_fingerprint):
    payload = {
        "view": view_key,
        "game_fingerprint": game_fingerprint,
        "model": power_model_signature(view_key),
        "inputs": [
            {
                "team_id": team["team_id"],
                "wins": team["wins"],
                "losses": team["losses"],
                "rs": team["rs"],
                "ra": team["ra"],
                "diff_per_game": round(team["diff_per_game"], 8),
                "win_pct_num": round(team["win_pct_num"], 8),
                "owp_num": round(team["owp_num"], 8),
            }
            for team in sorted(teams, key=lambda row: row["team_id"])
        ],
    }

    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def official_stats_for_team(team_id, official_lookup, fallback_games):
    official = official_lookup.get(team_id) if official_lookup else None

    if official:
        raw = official.get("raw", {})
        last10_record = find_split_record(raw, "lastTen")

        wins = official.get("wins", 0)
        losses = official.get("losses", 0)
        games = wins + losses
        rs = official.get("runs_scored", 0)
        ra = official.get("runs_allowed", 0)

        return {
            "wins": wins,
            "losses": losses,
            "games": games,
            "record": official.get("record", f"{wins}-{losses}"),
            "pct_num": official.get(
                "pct_num",
                wins / games if games else 0.0,
            ),
            "pct": official.get(
                "pct",
                format_pct(wins / games if games else 0.0),
            ),
            "rs": rs,
            "ra": ra,
            "diff": rs - ra,
            "last10": (
                format_record(last10_record)
                if last10_record
                else calculate_last_n_record(team_id, fallback_games)
            ),
            "source": "official_standings",
        }

    stats = calculate_team_game_stats(team_id, fallback_games)
    stats["last10"] = calculate_last_n_record(team_id, fallback_games)
    stats["source"] = "schedule_fallback"
    return stats


def build_base_view_teams(
    view_key,
    regular_season_lookup,
    official_lookup,
    games_by_team,
):
    teams = []

    for team_id in sorted(regular_season_lookup):
        regular_record = regular_season_lookup[team_id]
        games = games_by_team.get(team_id, [])

        if view_key == "recent":
            stats = calculate_team_game_stats(team_id, games)
            stats["last10"] = calculate_last_n_record(
                team_id,
                games,
            )
            stats["source"] = "schedule_recent_window"
        else:
            stats = official_stats_for_team(
                team_id,
                official_lookup,
                games,
            )

        games_count = stats["wins"] + stats["losses"]
        diff = stats["rs"] - stats["ra"]

        teams.append({
            "team_id": team_id,
            "team": regular_record["team"],
            "division": regular_record.get("division", "Unknown"),
            "division_name": regular_record.get("division_name", ""),
            "view": view_key,
            "stats_source": stats.get("source"),
            "record": stats["record"],
            "pct": stats["pct"],
            "rs": stats["rs"],
            "ra": stats["ra"],
            "last10": stats["last10"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "games": games_count,
            "games_in_view": len(games),
            "diff": diff,
            "win_pct_num": (
                stats["wins"] / games_count
                if games_count else 0.0
            ),
            "diff_per_game": (
                diff / games_count
                if games_count else 0.0
            ),
        })

    average_opponent_win_pct_by_id = (
        calculate_opponent_win_percentages_for_view(
            games_by_team,
        )
    )

    for team in teams:
        opponent_win_pct_num = average_opponent_win_pct_by_id.get(
            team["team_id"],
            0.500,
        )

        team["opponent_win_pct"] = format_pct(opponent_win_pct_num)
        team["owp"] = format_pct(opponent_win_pct_num)
        team["opponent_win_pct_num"] = opponent_win_pct_num
        team["owp_num"] = opponent_win_pct_num

    return teams


def add_common_team_context(
    teams,
    team_playoff_state,
    first_half_winners,
):
    for team in teams:
        team_id = team["team_id"]
        division_key = team.get("division", "Unknown")

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

        team["first_half_winner"] = (
            first_half_winners.get(division_key, {}).get("team_id")
            == team_id
        )
        team["playoff_clinched"] = playoff_state["playoff_clinched"]
        team["berth_type"] = playoff_state["berth_type"]
        team["active_playoff_race_eligible"] = playoff_state[
            "active_race_eligible"
        ]
        team["active_playoff_race_rank"] = playoff_state[
            "active_race_rank"
        ]
        team["active_playoff_race_games_back"] = playoff_state[
            "active_race_games_back"
        ]


def add_overall_legacy_fields(
    teams,
    regular_season_lookup,
    first_half_lookup,
    second_half_lookup,
    current_half_lookup,
    active_half,
):
    for team in teams:
        team_id = team["team_id"]
        regular_record = regular_season_lookup.get(team_id, {})
        raw = regular_record.get("raw", {})

        expected_record = find_expected_record(raw)
        vs500_record = find_split_record(raw, "winners")

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

        # Fields retained so the current live HTML continues to work before
        # the four-view front end is deployed.
        team["xwl"] = format_record(expected_record)
        team["vs500"] = format_record(vs500_record)
        team["identity"] = "TBD"

        team["first_half_record"] = first_half_values["record"]
        team["first_half_wins"] = first_half_values["wins"]
        team["first_half_losses"] = first_half_values["losses"]
        team["first_half_pct"] = first_half_values["pct"]
        team["first_half_pct_num"] = first_half_values["pct_num"]
        team["first_half_division_rank"] = (
            first_half_record.get("division_rank")
            if first_half_record else None
        )
        team["first_half_clinch_indicator"] = (
            first_half_record.get("clinch_indicator")
            if first_half_record else None
        )

        team["second_half_record"] = second_half_values["record"]
        team["second_half_wins"] = second_half_values["wins"]
        team["second_half_losses"] = second_half_values["losses"]
        team["second_half_pct"] = second_half_values["pct"]
        team["second_half_pct_num"] = second_half_values["pct_num"]
        team["second_half_division_rank"] = (
            second_half_record.get("division_rank")
            if second_half_record else None
        )
        team["second_half_games_back"] = (
            second_half_record.get("games_back")
            if second_half_record else None
        )
        team["second_half_clinch_indicator"] = (
            second_half_record.get("clinch_indicator")
            if second_half_record else None
        )

        team["current_half_record"] = current_half_values["record"]
        team["current_half_wins"] = current_half_values["wins"]
        team["current_half_losses"] = current_half_values["losses"]
        team["current_half_pct"] = current_half_values["pct"]
        team["current_half_pct_num"] = current_half_values["pct_num"]
        team["active_half"] = active_half


def validate_game_selection_against_official(
    view_key,
    games_by_team,
    official_lookup,
):
    warnings = []

    if not official_lookup:
        return warnings

    for team_id, official in official_lookup.items():
        games = games_by_team.get(team_id, [])
        calculated = calculate_team_game_stats(team_id, games)

        expected_wins = official.get("wins", 0)
        expected_losses = official.get("losses", 0)
        expected_rs = official.get("runs_scored", 0)
        expected_ra = official.get("runs_allowed", 0)

        mismatches = []

        if calculated["wins"] != expected_wins:
            mismatches.append(
                f"W {calculated['wins']} != {expected_wins}"
            )

        if calculated["losses"] != expected_losses:
            mismatches.append(
                f"L {calculated['losses']} != {expected_losses}"
            )

        # Only compare run totals when the standings endpoint provides them.
        raw = official.get("raw", {})

        if "runsScored" in raw and calculated["rs"] != expected_rs:
            mismatches.append(
                f"RS {calculated['rs']} != {expected_rs}"
            )

        if "runsAllowed" in raw and calculated["ra"] != expected_ra:
            mismatches.append(
                f"RA {calculated['ra']} != {expected_ra}"
            )

        if mismatches:
            warnings.append(
                f"{view_key}: schedule-selected games for "
                f"{official.get('team', team_id)} do not match official "
                f"standings ({'; '.join(mismatches)})."
            )

    return warnings


def apply_power_index(
    view_key,
    teams,
    input_fingerprint,
    previous_snapshot,
    comparison_snapshot,
):
    if not teams:
        return [], False

    previous_team_map = get_snapshot_team_map(
        previous_snapshot,
        view_key,
    )

    previous_model_compatible = (
        previous_snapshot.get("model_version") == POWER_MODEL_VERSION
    )
    comparison_model_compatible = (
        comparison_snapshot.get("model_version") == POWER_MODEL_VERSION
    )

    # Weekly movement must compare like with like. A model-version change can
    # legitimately move ratings even with identical games, so an older-model
    # baseline is not treated as a team-strength trend.
    comparison_team_map = (
        get_snapshot_team_map(comparison_snapshot, view_key)
        if comparison_model_compatible
        else {}
    )

    previous_fingerprint = get_snapshot_view_fingerprint(
        previous_snapshot,
        view_key,
    )

    data_changed = previous_fingerprint != input_fingerprint

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
            POWER_DIFF_WEIGHT * diff_score
            + POWER_WIN_PCT_WEIGHT * actual_win_score
            + POWER_OWP_WEIGHT * opponent_strength_score
        )

        previous_team = previous_team_map.get(team["team"])
        previous_smoothed_power_score = get_previous_smoothed_power(
            previous_team,
            raw_power_score,
        )
        smoothing = get_view_smoothing_config(view_key)

        if not smoothing["enabled"]:
            # Recent is already stabilized by its rolling 24-game sample.
            # Publishing the current raw score directly ensures that games
            # leaving that window no longer influence Recent Power indirectly
            # through an older smoothing state.
            smoothed_power_score = raw_power_score
            compressed_power_score = compress_power_score(
                smoothed_power_score
            )
        elif previous_team and not previous_model_compatible:
            # Do not blend a new formula with smoothing state created by an
            # older model. This is especially important for a frozen view such
            # as First Half, which otherwise could retain the old model forever.
            smoothed_power_score = raw_power_score
            compressed_power_score = compress_power_score(
                smoothed_power_score
            )
        elif previous_team and not data_changed:
            smoothed_power_score = previous_smoothed_power_score

            # If the underlying model inputs are unchanged, carry forward the
            # exact previously published Power score. This prevents a repeated
            # workflow run from moving a rating by 0.1 solely because an older
            # snapshot stored the smoothing intermediate at one decimal place.
            compressed_power_score = get_previous_display_power(
                previous_team,
                compress_power_score(smoothed_power_score),
            )
        elif previous_team:
            smoothed_power_score = (
                previous_smoothed_power_score
                * smoothing["previous_weight"]
                + raw_power_score
                * smoothing["raw_weight"]
            )
            compressed_power_score = compress_power_score(
                smoothed_power_score
            )
        else:
            # New views begin from their current raw score rather than from an
            # arbitrary neutral value. Compression is still applied below.
            smoothed_power_score = raw_power_score
            compressed_power_score = compress_power_score(
                smoothed_power_score
            )

        previous_power_score = get_previous_display_power(
            previous_team,
            compressed_power_score,
        )

        comparison_team = comparison_team_map.get(team["team"])
        comparison_power_score = (
            comparison_team.get("power_score", compressed_power_score)
            if comparison_team else compressed_power_score
        )

        power_delta = (
            compressed_power_score
            - comparison_power_score
        )

        power_delta_direction = get_power_delta_direction(power_delta)

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
        team["smoothing_enabled"] = smoothing["enabled"]

        # Preserve the smoothing state at full floating-point precision for
        # the next run. The *_score fields remain rounded for readable JSON
        # and debugging, but they are no longer used as the primary state.
        team["previous_smoothed_power_state"] = (
            previous_smoothed_power_score
        )
        team["smoothed_power_state"] = smoothed_power_score
        team["previous_smoothed_power_score"] = round(
            previous_smoothed_power_score,
            1,
        )
        team["smoothed_power_score"] = round(
            smoothed_power_score,
            1,
        )

        # Retain the old field name as an alias for compatibility/debugging.
        team["displayed_power_score"] = round(
            smoothed_power_score,
            1,
        )
        team["previous_power_score"] = round(
            previous_power_score,
            1,
        )
        team["comparison_power_score"] = round(
            comparison_power_score,
            1,
        )
        team["power_score"] = round(compressed_power_score, 1)
        team["power_delta"] = round(power_delta, 1)
        team["power_delta_display"] = format_power_delta(power_delta)
        team["power_delta_direction"] = power_delta_direction
        team["power_delta_class"] = f"delta-{power_delta_direction}"

    teams = sorted(
        teams,
        key=lambda team: team["power_score"],
        reverse=True,
    )

    comparison_rank_map = {
        name: team.get("rank")
        for name, team in comparison_team_map.items()
        if team.get("rank") is not None
    }

    for index, team in enumerate(teams, start=1):
        team["rank"] = index

        comparison_rank = comparison_rank_map.get(team["team"])

        if comparison_rank is None:
            team["trend"] = "→"
        elif index < comparison_rank:
            team["trend"] = "↑"
        elif index > comparison_rank:
            team["trend"] = "↓"
        else:
            team["trend"] = "→"

    return teams, data_changed


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
comparison_snapshot = load_json_file(comparison_snapshot_path)
previous_snapshot = load_json_file(OUTPUT_PATH)

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

texas_league_team_ids = set(regular_season_lookup)

if not texas_league_team_ids:
    raise RuntimeError(
        "No Texas League teams were found in regular-season standings."
    )

print("\nTexas League team IDs:")
print(sorted(texas_league_team_ids))

completed_games = get_completed_games(
    texas_league_team_ids,
    season_start_date,
    today.isoformat(),
)

all_games_by_team = build_team_game_history(
    completed_games,
    texas_league_team_ids,
)

view_game_sets = build_view_game_sets(
    all_games_by_team,
    first_half_lookup,
    second_half_lookup,
    active_half,
)

previous_games = build_previous_games(completed_games)

view_official_lookups = {
    "overall": regular_season_lookup,
    "first_half": first_half_lookup,
    "second_half": second_half_lookup,
    "recent": {},
}

view_validation_warnings = []

for view_key in ("overall", "first_half", "second_half"):
    official_lookup = view_official_lookups[view_key]

    if official_lookup:
        view_validation_warnings.extend(
            validate_game_selection_against_official(
                view_key,
                view_game_sets[view_key],
                official_lookup,
            )
        )

views = {}

for view_key, config in VIEW_CONFIG.items():
    official_lookup = view_official_lookups[view_key]
    games_by_team = view_game_sets[view_key]

    view_teams = build_base_view_teams(
        view_key,
        regular_season_lookup,
        official_lookup,
        games_by_team,
    )

    add_common_team_context(
        view_teams,
        team_playoff_state,
        first_half_winners,
    )

    if view_key == "overall":
        add_overall_legacy_fields(
            view_teams,
            regular_season_lookup,
            first_half_lookup,
            second_half_lookup,
            current_half_lookup,
            active_half,
        )

    game_fingerprint = build_game_fingerprint(
        view_key,
        games_by_team,
    )

    input_fingerprint = build_input_fingerprint(
        view_key,
        view_teams,
        game_fingerprint,
    )

    ranked_teams, data_changed = apply_power_index(
        view_key,
        view_teams,
        input_fingerprint,
        previous_snapshot,
        comparison_snapshot,
    )

    team_game_counts = {
        team["team"]: team["games_in_view"]
        for team in ranked_teams
    }

    views[view_key] = {
        "key": view_key,
        "label": config["label"],
        "description": config["description"],
        "standings_type": config["standings_type"],
        "window_type": (
            "rolling"
            if view_key == "recent"
            else "fixed_then_frozen"
            if view_key == "first_half"
            else "expanding"
        ),
        "max_games_per_team": (
            RECENT_MAX_GAMES
            if view_key == "recent" else None
        ),
        "unique_games_selected": unique_game_count(games_by_team),
        "team_game_counts": team_game_counts,
        "game_fingerprint": game_fingerprint,
        "input_fingerprint": input_fingerprint,
        "data_changed_since_previous_run": data_changed,
        "previous_model_compatible": (
            previous_snapshot.get("model_version") == POWER_MODEL_VERSION
        ),
        "comparison_model_compatible": (
            comparison_snapshot.get("model_version") == POWER_MODEL_VERSION
        ),
        "smoothing": get_view_smoothing_config(view_key),
        "teams": ranked_teams,
    }

# Backward-compatible alias used by the current live page.
teams = views["overall"]["teams"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


playoff_validation_warnings = []
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
    playoff_validation_warnings.append(
        "Expected 10 Texas League teams in regularSeason, "
        f"found {regular_team_count}."
    )

if unknown_division_teams:
    playoff_validation_warnings.append(
        "Could not resolve a division for: "
        + ", ".join(unknown_division_teams)
        + "."
    )

if detected_divisions != EXPECTED_DIVISIONS:
    playoff_validation_warnings.append(
        "Expected playoff races for North and South, but detected "
        f"{sorted(detected_divisions)}."
    )

if active_half is None:
    playoff_validation_warnings.append(
        "The active half could not be determined."
    )

if (
    active_half == 2
    and first_half_winner_divisions != EXPECTED_DIVISIONS
):
    playoff_validation_warnings.append(
        "Second half was detected, but the script did not identify "
        "the first-half champions for both North and South."
    )

if active_half == 1 and first_half_team_count == 0:
    playoff_validation_warnings.append(
        "First half was detected, but firstHalf standings are empty."
    )

if active_half == 2 and second_half_team_count == 0:
    playoff_validation_warnings.append(
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
        playoff_validation_warnings.append(
            f"Expected 5 teams in the {division_key} Division, "
            f"found {actual_division_team_count}."
        )

    if active_half == 2:
        eligible_team_count = len(race.get("eligible_teams", []))

        if eligible_team_count != 4:
            playoff_validation_warnings.append(
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
    and not playoff_validation_warnings
)

validation_warnings = (
    playoff_validation_warnings
    + view_validation_warnings
)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


output = {
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "season": season,
    "model_version": POWER_MODEL_VERSION,
    "previous_games": previous_games,

    # Existing field retained for the current page.
    "data_source": "regularSeason",

    "standings_sources": {
        # Legacy meaning retained: the default/top-level table is Overall.
        "power_index": "regularSeason",
        "overall": "regularSeason",
        "first_half": "firstHalf",
        "second_half": "secondHalf",
        "recent": "schedule_last_24",
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
        "playoff_warnings": playoff_validation_warnings,
        "view_game_selection_warnings": view_validation_warnings,
        "warnings": validation_warnings,
    },

    "view_definitions": {
        "overall": (
            "All completed regular-season games for each team."
        ),
        "first_half": (
            "Official first-half standings and each team's first "
            "official first-half game count for schedule-based OWP."
        ),
        "second_half": (
            "Official second-half standings and each team's most recent "
            "official second-half game count for schedule-based OWP."
        ),
        "recent": (
            "Each team's most recent up to 24 completed regular-season "
            "games. Before 24 games have been played, all available games "
            "are used. Once 24 are available, older games roll out as new "
            "games are completed."
        ),
    },

    "trend_basis": (
        "Within each view, rank arrows compare against that same view's "
        "weekly baseline snapshot from Monday or the first available "
        "snapshot of the current week."
    ),

    "power_delta_basis": (
        "Within each view, Power delta compares against the same weekly "
        "baseline snapshot used for that view's rank arrows."
    ),

    "comparison_snapshot": (
        str(comparison_snapshot_path)
        if comparison_snapshot_path else None
    ),

    "power_formula": {
        "formula": (
            "raw_power_score = 50% Run Differential Per Game "
            "+ 25% Actual Winning Percentage "
            "+ 25% Average Opponent Winning Percentage"
        ),
        "diff": (
            "Run Differential Per Game = normalized run differential "
            "per game within the selected view"
        ),
        "actual_winning_percentage": (
            "Actual Winning Percentage = normalized winning percentage "
            "within the selected view"
        ),
        "opponent_strength": (
            "Average Opponent Winning Percentage = opponents' winning "
            "percentages within the same selected view after excluding games "
            "against the team being evaluated, bounded-normalized against "
            ".450 to .550"
        ),
    },

    "power_smoothing": {
        "enabled": True,
        "data_change_driven": True,
        "full_precision_state": True,
        "default": {
            "enabled": True,
            "previous_weight": POWER_SMOOTHING_PREVIOUS_WEIGHT,
            "raw_weight": POWER_SMOOTHING_RAW_WEIGHT,
        },
        "recent": get_view_smoothing_config("recent"),
        "formula": (
            "Overall, First Half, and Second Half smooth changed inputs as "
            "previous_smoothed_power_score * 0.75 + raw_power_score * 0.25. "
            "Recent uses no additional smoothing because the rolling 24-game "
            "window already provides temporal smoothing."
        ),
        "state_note": (
            "The internal smoothed_power_state is stored without display "
            "rounding for smoothed views. Recent stores its current raw score "
            "in the same field for schema compatibility. When the model version "
            "changes, smoothed views reset to the new raw score instead of "
            "blending incompatible model states."
        ),
    },

    "power_compression": {
        "enabled": True,
        "center": POWER_COMPRESSION_CENTER,
        "factor": POWER_COMPRESSION_FACTOR,
        "formula": (
            "power_score = 50 + "
            "((smoothed_power_score - 50) * 0.75)"
        ),
        "note": (
            "Compression is applied once after smoothing and is not fed "
            "back into the next smoothing step."
        ),
    },

    "power_delta": {
        "enabled": True,
        "neutral_threshold": 0,
        "formula": (
            "power_delta = power_score - comparison_power_score"
        ),
    },

    "owp": {
        "enabled": True,
        "season_start_date": season_start_date,
        "lower_bound": OWP_LOWER_BOUND,
        "upper_bound": OWP_UPPER_BOUND,
        "exclude_head_to_head": OWP_EXCLUDE_HEAD_TO_HEAD,
        "empty_adjusted_record_fallback": ".500",
        "formula": (
            "For each selected team game, use that opponent's winning "
            "percentage from the same view after excluding all games against "
            "the team being evaluated, then average those opponent values. "
            "Opponents remain weighted by games played. If an opponent has no "
            "independent games after the exclusion, use neutral .500. OWP "
            "score is bounded-normalized so .450 = 0, .500 = 50, and "
            ".550 = 100."
        ),
        # Legacy field retained for the current page/debug output.
        "completed_games_used": len(completed_games),
        "completed_regular_season_games_fetched": len(completed_games),
    },

    "recent_window": {
        "max_games_per_team": RECENT_MAX_GAMES,
        "minimum_games_required": 0,
        "rolling": True,
    },

    # New multi-view structure for the future tabbed interface.
    "views": views,

    # Backward-compatible alias: the current site can continue reading teams.
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
print(f"Model version: {POWER_MODEL_VERSION}")
print(f"Active half: {active_half}")
print(f"Active-half detection: {active_half_detection_method}")
print(f"Detected divisions: {sorted(detected_divisions)}")
print(f"First-half winners found: {first_half_winner_count}")
print(
    "First-half winner divisions: "
    f"{sorted(first_half_winner_divisions)}"
)
print(f"Playoff state valid: {playoff_state_valid}")

for view_key in VIEW_CONFIG:
    view = views[view_key]
    print(
        f"View {view_key}: "
        f"{len(view['teams'])} teams, "
        f"{view['unique_games_selected']} unique selected games, "
        f"data_changed={view['data_changed_since_previous_run']}"
    )

if validation_warnings:
    print("Validation warnings:")

    for warning in validation_warnings:
        print(f"- {warning}")

print(f"Teams written to legacy alias: {len(teams)}")
print(f"Previous games written: {len(previous_games)}")
print(
    "Completed regular-season games fetched: "
    f"{len(completed_games)}"
)
print(f"Last updated: {output['last_updated']}")
