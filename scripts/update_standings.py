import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

URL = "https://statsapi.mlb.com/api/v1/standings?sportId=11&leagueId=109&season=2026&standingsTypes=regularSeason"

OUTPUT_PATH = Path("data/standings.json")
HISTORY_DIR = Path("data/history")

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
    if not path.exists():
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

def find_comparison_snapshot(today):
    target_date = today - timedelta(days=7)

    for offset in range(0, 8):
        for candidate_date in [
            target_date - timedelta(days=offset),
            target_date + timedelta(days=offset),
        ]:
            candidate_path = HISTORY_DIR / f"{candidate_date.isoformat()}.json"
            if candidate_path.exists():
                return candidate_path

    return None

today = datetime.now(timezone.utc).date()
comparison_snapshot_path = find_comparison_snapshot(today)
previous_ranks = load_rank_snapshot(comparison_snapshot_path) if comparison_snapshot_path else {}

if comparison_snapshot_path:
    print(f"Comparing trends against {comparison_snapshot_path}")
else:
    print("No 7-day comparison snapshot found. Trends will default to sideways.")

response = requests.get(URL)
response.raise_for_status()
data = response.json()

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
        display_team_name = TEAM_NAMES.get(raw_team_name, raw_team_name)

        expected_record = find_expected_record(team_data)
        xwl = format_record(expected_record)

        if expected_record:
            x_wins = expected_record.get("wins", 0)
            x_losses = expected_record.get("losses", 0)
            x_win_pct_num = x_wins / (x_wins + x_losses) if (x_wins + x_losses) else 0
        else:
            x_win_pct_num = wins / games if games else 0

        last10_record = find_split_record(team_data, "lastTen")
        vs500_record = find_split_record(team_data, "winners")

        vs500_wins = vs500_record.get("wins", 0) if vs500_record else 0
        vs500_losses = vs500_record.get("losses", 0) if vs500_record else 0
        vs500_games = vs500_wins + vs500_losses
        vs500_win_pct_num = vs500_wins / vs500_games if vs500_games else 0
        vs500_game_share = vs500_games / games if games else 0

        team = {
            "team": display_team_name,
            "record": f"{wins}-{losses}",
            "pct": team_data.get("winningPercentage", f"{wins / games:.3f}" if games else ".000"),
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

def normalize(value, values, reverse=False):
    min_value = min(values)
    max_value = max(values)

    if max_value == min_value:
        return 50

    score = 100 * (value - min_value) / (max_value - min_value)
    return 100 - score if reverse else score

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
        + 0.10 * normalize(team["rs_per_game"], offense_values)
        + 0.05 * normalize(team["ra_per_game"], defense_values, reverse=True)
    )

    quality_record_score = (
        0.70 * normalize(team["vs500_win_pct_num"], vs500_values)
        + 0.30 * normalize(team["vs500_game_share"], vs500_share_values)
    )

    team["run_profile_score"] = round(run_profile_score, 1)
    team["quality_record_score"] = round(quality_record_score, 1)

    team["power_score"] = round(
        0.50 * run_profile_score
        + 0.25 * normalize(team["win_pct_num"], actual_win_values)
        + 0.25 * quality_record_score,
        1
    )

teams = sorted(teams, key=lambda team: team["power_score"], reverse=True)

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
    "trend_basis": "Compared against closest available ranking snapshot from 7 days ago.",
    "comparison_snapshot": str(comparison_snapshot_path) if comparison_snapshot_path else None,
    "teams": teams
}

OUTPUT_PATH.parent.mkdir(exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

with OUTPUT_PATH.open("w", encoding="utf-8") as f:
    json.dump(output, f, indent=2)

history_path = HISTORY_DIR / f"{today.isoformat()}.json"

with history_path.open("w", encoding="utf-8") as f:
    json.dump(output, f, indent=2)

print(f"Wrote {OUTPUT_PATH}")
print(f"Wrote {history_path}")
print(f"Teams written: {len(teams)}")
print(f"Last updated: {output['last_updated']}")
