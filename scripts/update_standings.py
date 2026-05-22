import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

URL = "https://statsapi.mlb.com/api/v1/standings?sportId=11&leagueId=109&season=2026&standingsTypes=regularSeason"

SCHEDULE_URL = (
    "https://statsapi.mlb.com/api/v1/schedule"
    "?sportId=11"
    "&startDate={date}"
    "&endDate={date}"
    "&hydrate=team,linescore"
)

OUTPUT_PATH = Path("data/standings.json")
HISTORY_DIR = Path("data/history")

POWER_SMOOTHING_PREVIOUS_WEIGHT = 0.75
POWER_SMOOTHING_RAW_WEIGHT = 0.25

POWER_COMPRESSION_CENTER = 50
POWER_COMPRESSION_FACTOR = 0.75

POWER_DELTA_NEUTRAL_THRESHOLD = 0.3

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


def normalize_team_name(name):
    return TEAM_NAMES.get(name, name)


def format_record(record):
    if not record:
        return "0-0"

    return f"{record.get('wins', 0)}-{record.get('losses', 0)}"


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


def format_power_delta(delta):
    if abs(delta) < POWER_DELTA_NEUTRAL_THRESHOLD:
        return "0.0"

    if delta > 0:
        return f"+{delta:.1f}"

    return f"{delta:.1f}"


def get_power_delta_direction(delta):
    if delta >= POWER_DELTA_NEUTRAL_THRESHOLD:
        return "up"

    if delta <= -POWER_DELTA_NEUTRAL_THRESHOLD:
        return "down"

    return "neutral"


def get_previous_games(texas_league_team_ids, max_games=5, max_days_back=10):
    previous_games = []
    seen_game_pks = set()

    start_date = datetime.now(timezone.utc).date() - timedelta(days=1)

    for offset in range(max_days_back):
        game_date = start_date - timedelta(days=offset)

        url = SCHEDULE_URL.format(date=game_date.isoformat())

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

                away_debug = game.get("teams", {}).get("away", {}).get("team", {})
                home_debug = game.get("teams", {}).get("home", {}).get("team", {})

                print(
                    game.get("gameDate"),
                    away_debug.get("id"),
                    away_debug.get("name"),
                    "at",
                    home_debug.get("id"),
                    home_debug.get("name"),
                    game.get("status", {}).get("abstractGameState"),
                    game.get("status", {}).get("detailedState")
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


today = datetime.now(timezone.utc).date()

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
        f"Comparing trends and power deltas against weekly baseline "
        f"{comparison_snapshot_path}"
    )
else:
    print(
        "No weekly baseline snapshot found. "
        "Trends and power deltas will default to neutral."
    )

response = requests.get(URL, timeout=20)
response.raise_for_status()

data = response.json()

texas_league_team_ids = set()

for division in data["records"]:
    for team_data in division["teamRecords"]:
        texas_league_team_ids.add(team_data["team"]["id"])

print("\nTexas League team IDs:")
print(sorted(texas_league_team_ids))

previous_games = get_previous_games(texas_league_team_ids)

teams = []

for division in data["records"]:
    for team_data in division["teamRecords"]:

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

        if expected_record:
            x_wins = expected_record.get("wins", 0)
            x_losses = expected_record.get("losses", 0)

            x_win_pct_num = (
                x_wins / (x_wins + x_losses)
                if (x_wins + x_losses)
                else 0
            )

        else:
            x_win_pct_num = wins / games if games else 0

        last10_record = find_split_record(team_data, "lastTen")

        vs500_record = find_split_record(team_data, "winners")

        vs500_wins = (
            vs500_record.get("wins", 0)
            if vs500_record else 0
        )

        vs500_losses = (
            vs500_record.get("losses", 0)
            if vs500_record else 0
        )

        vs500_games = vs500_wins + vs500_losses

        vs500_win_pct_num = (
            vs500_wins / vs500_games
            if vs500_games else 0
        )

        vs500_game_share = (
            vs500_games / games
            if games else 0
        )

        team = {
            "team": display_team_name,
            "record": f"{wins}-{losses}",
            "pct": team_data.get(
                "winningPercentage",
                f"{wins / games:.3f}" if games else ".000"
            ),
            "rs": rs,
            "ra": ra,
            "xwl": xwl,
            "last10": format_record(last10_record),
            "vs500": format_record(vs500_record),
            "identity": "TBD",
            "wins": wins,
            "losses": losses,
            "diff": diff,
            "win_pct_num": wins / games if games else 0,
            "x_win_pct_num": x_win_pct_num,
            "vs500_win_pct_num": vs500_win_pct_num,
            "vs500_game_share": vs500_game_share,
            "diff_per_game": diff / games if games else 0,
            "rs_per_game": rs / games if games else 0,
            "ra_per_game": ra / games if games else 0,
        }

        teams.append(team)

diff_values = [team["diff_per_game"] for team in teams]
x_win_values = [team["x_win_pct_num"] for team in teams]
actual_win_values = [team["win_pct_num"] for team in teams]
offense_values = [team["rs_per_game"] for team in teams]
defense_values = [team["ra_per_game"] for team in teams]
vs500_values = [team["vs500_win_pct_num"] for team in teams]
vs500_share_values = [team["vs500_game_share"] for team in teams]

for team in teams:

    run_profile_score = (
        0.60 * normalize(team["diff_per_game"], diff_values)
        + 0.25 * normalize(team["x_win_pct_num"], x_win_values)
        + 0.075 * normalize(team["rs_per_game"], offense_values)
        + 0.075 * normalize(
            team["ra_per_game"],
            defense_values,
            reverse=True
        )
    )

    quality_record_score = (
        0.70 * normalize(
            team["vs500_win_pct_num"],
            vs500_values
        )
        + 0.30 * normalize(
            team["vs500_game_share"],
            vs500_share_values
        )
    )

    raw_power_score = (
        0.50 * run_profile_score
        + 0.25 * normalize(
            team["win_pct_num"],
            actual_win_values
        )
        + 0.25 * quality_record_score
    )

    previous_power_score = previous_powers.get(
        team["team"],
        raw_power_score
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
        compressed_power_score
    )

    power_delta = (
        compressed_power_score
        - comparison_power_score
    )

    power_delta_direction = get_power_delta_direction(
        power_delta
    )

    team["run_profile_score"] = round(run_profile_score, 1)
    team["quality_record_score"] = round(quality_record_score, 1)
    team["raw_power_score"] = round(raw_power_score, 1)
    team["previous_power_score"] = round(previous_power_score, 1)
    team["displayed_power_score"] = round(displayed_power_score, 1)
    team["comparison_power_score"] = round(comparison_power_score, 1)
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
    reverse=True
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

output = {
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "previous_games": previous_games,

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

    "power_smoothing": {
        "enabled": True,
        "previous_weight": POWER_SMOOTHING_PREVIOUS_WEIGHT,
        "raw_weight": POWER_SMOOTHING_RAW_WEIGHT,
        "formula": (
            "displayed_power_score = previous_power_score "
            "* 0.75 + raw_power_score * 0.25"
        )
    },

    "power_compression": {
        "enabled": True,
        "center": POWER_COMPRESSION_CENTER,
        "factor": POWER_COMPRESSION_FACTOR,
        "formula": (
            "power_score = 50 + "
            "((displayed_power_score - 50) * 0.75)"
        )
    },

    "power_delta": {
        "enabled": True,
        "neutral_threshold": POWER_DELTA_NEUTRAL_THRESHOLD,
        "formula": (
            "power_delta = "
            "power_score - comparison_power_score"
        )
    },

    "teams": teams
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

print(f"Teams written: {len(teams)}")

print(f"Previous games written: {len(previous_games)}")

print(f"Last updated: {output['last_updated']}")
