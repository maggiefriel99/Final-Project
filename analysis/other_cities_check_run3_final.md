# Other cities' 311 complaints (run3_final)

300 public complaints from 6 cities (SeeClickFix), resident wording, run through Triage and the Life-Safety Screener.

- Crashes: 0
- Routes: {'emergency': 3, 'operating': 211, 'human_review': 46, 'capital': 18, 'not_city': 22}
- Complaint type plausible for the city's own category: 159 of 238 scored ({'albuquerque': '20 of 39', 'chicago': '31 of 46', 'detroit': '35 of 44', 'houston': '24 of 37', 'new-haven': '26 of 43', 'richmond': '23 of 29'})
- Ordinary text flagged by the manipulation patterns: 0; by an AI agent: 2; blocked by the provider: 0
- NYC borough invented for a non-NYC complaint: 2; NYC district assigned: 10
- Escalated as possible emergencies: 3; urgent hazards (alert marked urgent, no 911): 22; held for danger words only: 1
- Personal information removed in: 1
- Median seconds per request: 2.4

Why requests were held:

- The agent had low confidence in its reading of the request.: 41
- The Life-Safety Screener flagged text that looks like instructions to the system.: 1
- The request uses words that can signal danger to life ("…"), but neither AI check found an emergency. A person: 1
- The agent flagged text that looks like instructions to the system.: 1
- The agent's answer can't be passed on as-is: numbers not in the request: 2: 1
- The agent's answer can't be passed on as-is: numbers not in the request: 450: 1
