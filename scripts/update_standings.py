import json
from pathlib import Path
import requests

URL = "https://statsapi.mlb.com/api/v1/standings?sportId=11&leagueId=109&season=2026&standingsTypes=regularSeason"

response = requests.get(URL)
data = response.json()

print(json.dumps(data, indent=2))
```
