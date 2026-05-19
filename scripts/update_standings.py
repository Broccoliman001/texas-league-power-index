import json
from pathlib import Path
import requests

URL = "https://statsapi.mlb.com/api/v1/standings?sportId=11&leagueId=109&season=2026&standingsTypes=regularSeason"

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

        last10 = team_data.get("records", {}).get("lastTen", {})
        last10_wins = last10.get("wins", 0)
        last10_losses = last10.get("losses", 0)

        expected = team_data.get("records", {}).get("expectedRecords", {})
        xwl = "0-0"

        if isinstance(expected, list) and len(expected) > 0:
            x_wins = expected[0].get("wins", 0)
            x_losses = expected[0].get("losses", 0)
            xwl = f"{x_wins}-{x_losses}"
            x_win_pct_num = x_wins / (x_wins + x_losses) if (x_wins + x_losses) else 0
        else:
            x_win_pct_num = wins / games if games else 0

        vs_500 = team_data.get("records", {}).get("vsWinning", {})
        vs500_wins = vs_500.get("wins", 0)
        vs500_losses = vs_500.get("losses", 0)

        team = {
            "team": team_data["team"]["name"],
            "record": f"{wins}-{losses}",
            "pct": team_data.get("winningPercentage", f"{wins / games:.3f}" if games else ".000"),
            "rs": rs,
            "ra": ra,
            "xwl": xwl,
            "last10": f"{last10_wins}-{last10_losses}",
            "vs500": f"{vs500_wins}-{vs500_losses}",
            "trend": "→",
            "identity": "TBD",
            "wins": wins,
            "losses": losses,
            "diff": diff,
            "win_pct_num": wins / games if games else 0,
            "x_win_pct_num": x_win_pct_num,
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

for team in teams:
    team["power_score"] = round(
        0.40 * normalize(team["diff_per_game"], diff_values)
        + 0.25 * normalize(team["x_win_pct_num"], x_win_values)
        + 0.15 * normalize(team["win_pct_num"], actual_win_values)
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
print(f"Teams written: {len(teams)}")
