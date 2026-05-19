import json
from pathlib import Path

# V1: manual source data in Python.
# Next phase: replace this list with data fetched from MLB/MiLB API.

teams = [
    {
        "team": "Arkansas",
        "record": "24-15",
        "pct": ".615",
        "rs": 215,
        "ra": 160,
        "xwl": "25-14",
        "last10": "7-3",
        "vs500": "19-14",
        "trend": "→",
        "identity": "Run Prevention Machine"
    },
    {
        "team": "Frisco",
        "record": "20-18",
        "pct": ".526",
        "rs": 251,
        "ra": 216,
        "xwl": "22-16",
        "last10": "5-5",
        "vs500": "14-15",
        "trend": "↑",
        "identity": "Offensive Juggernaut"
    },
    {
        "team": "Tulsa",
        "record": "22-17",
        "pct": ".564",
        "rs": 234,
        "ra": 209,
        "xwl": "22-17",
        "last10": "4-6",
        "vs500": "9-9",
        "trend": "↓",
        "identity": "Cooling Contender"
    },
    {
        "team": "Midland",
        "record": "22-17",
        "pct": ".564",
        "rs": 187,
        "ra": 182,
        "xwl": "20-19",
        "last10": "3-7",
        "vs500": "11-10",
        "trend": "↓",
        "identity": "Steady Survivor"
    },
    {
        "team": "Wichita",
        "record": "19-19",
        "pct": ".500",
        "rs": 211,
        "ra": 202,
        "xwl": "20-18",
        "last10": "5-5",
        "vs500": "11-15",
        "trend": "→",
        "identity": "Quietly Competent"
    },
    {
        "team": "Amarillo",
        "record": "20-18",
        "pct": ".526",
        "rs": 203,
        "ra": 226,
        "xwl": "17-21",
        "last10": "6-4",
        "vs500": "11-12",
        "trend": "↑",
        "identity": "Fraud Watch"
    },
    {
        "team": "Northwest Arkansas",
        "record": "19-19",
        "pct": ".500",
        "rs": 218,
        "ra": 233,
        "xwl": "18-20",
        "last10": "4-6",
        "vs500": "13-13",
        "trend": "↓",
        "identity": "Regression Watch"
    },
    {
        "team": "Springfield",
        "record": "16-23",
        "pct": ".410",
        "rs": 206,
        "ra": 229,
        "xwl": "18-21",
        "last10": "6-4",
        "vs500": "12-21",
        "trend": "↑",
        "identity": "Better Than Record"
    },
    {
        "team": "Corpus Christi",
        "record": "17-22",
        "pct": ".436",
        "rs": 194,
        "ra": 198,
        "xwl": "19-20",
        "last10": "4-6",
        "vs500": "11-16",
        "trend": "→",
        "identity": "Perfectly Bland"
    },
    {
        "team": "San Antonio",
        "record": "14-25",
        "pct": ".359",
        "rs": 150,
        "ra": 214,
        "xwl": "13-26",
        "last10": "6-4",
        "vs500": "12-21",
        "trend": "→",
        "identity": "Schedule Gift"
    },
]

for team in teams:
    wins, losses = map(int, team["record"].split("-"))
    x_wins, x_losses = map(int, team["xwl"].split("-"))

    games = wins + losses
    team["wins"] = wins
    team["losses"] = losses
    team["diff"] = team["rs"] - team["ra"]
    team["win_pct_num"] = wins / games
    team["x_win_pct_num"] = x_wins / (x_wins + x_losses)
    team["diff_per_game"] = team["diff"] / games
    team["rs_per_game"] = team["rs"] / games
    team["ra_per_game"] = team["ra"] / games

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
