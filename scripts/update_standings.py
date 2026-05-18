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

# Simple power order for now.
# Later this will become a true formula.
power_order = [
    "Arkansas",
    "Frisco",
    "Tulsa",
    "Midland",
    "Wichita",
    "Amarillo",
    "Northwest Arkansas",
    "Springfield",
    "Corpus Christi",
    "San Antonio",
]

order_lookup = {team: index + 1 for index, team in enumerate(power_order)}

for team in teams:
    team["rank"] = order_lookup[team["team"]]
    team["diff"] = team["rs"] - team["ra"]

teams = sorted(teams, key=lambda team: team["rank"])

output_path = Path("data/standings.json")
output_path.parent.mkdir(exist_ok=True)

with output_path.open("w", encoding="utf-8") as f:
    json.dump(teams, f, indent=2)

print(f"Wrote {output_path}")
