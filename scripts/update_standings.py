import json
from pathlib import Path
import requests

URL = "https://statsapi.mlb.com/api/v1/standings?sportId=11&leagueId=109&season=2026&standingsTypes=regularSeason"

response = requests.get(URL)
data = response.json()

records = data["records"][0]["teamRecords"]

teams = []

for team_data in records:
    team = {}

    wins = team_data["wins"]
    losses = team_data["losses"]
    games = wins + losses

    rs = team_data.get("runsScored", 0)
    ra = team_data.get("runsAllowed", 0)

    diff = rs - ra

    team["team"] = team_data["team"]["name"]
    team["record"] = f"{wins}-{losses}"
    team["pct"] = f"{team_data['winningPercentage']}"
    team["rs"] = rs
    team["ra"] = ra
    team["diff"] = diff

    # Placeholder values for now
    team["xwl"] = "0-0"
    team["last10"] = "0-0"
    team["vs500"] = "0-0"
    team["trend"] = "→"
    team["identity"] = "TBD"

    team["wins"] = wins
    team["losses"] = losses
    team["win_pct_num"] = wins / games if games else 0
    team["diff_per_game"] = diff / games if games else 0
    team["rs_per_game"] = rs / games if games else 0
    team["ra_per_game"] = ra / games if games else 0

    teams.append(team)

def normalize(value, values, reverse=False):
    min_value = min(values)
    max_value = max(values)

    if max_value == min_value:
        return 50

    score = 100 * (value - min_value) / (max_value - min_value)
    return 100 - score if reverse else score

diff_values = [team["diff_per_game"] for team in teams]
win_values = [team["win_pct_num"] for team in teams]
offense_values = [team["rs_per_game"] for team in teams]
defense_values = [team["ra_per_game"] for team in teams]

for team in teams:
    team["power_score"] = round(
        0.50 * normalize(team["diff_per_game"], diff_values)
        + 0.30 * normalize(team["win_pct_num"], win_values)
        + 0.10 * normalize(team["rs_per_game"], offense_values)
        + 0.10 * normalize(team["ra_per_game"], defense_values, reverse=True),
        1
    )

teams = sorted(teams, key=lambda team: team["power_score"], reverse=True)

for index, team in enumerate(teams, start=1):
    team["rank"] = index

output_path = Path("data/standings.json")
output_path.parent.mkdir(exist_ok=True)

with output_path.open("w", encoding="utf-8") as f:
    json.dump(teams, f, indent=2)

print(f"Wrote {output_path}")
