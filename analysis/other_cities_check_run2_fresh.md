# Other cities' 311 complaints (run2_fresh)

360 public complaints from 6 cities (SeeClickFix), resident wording, run through Triage and the Life-Safety Screener.

- Crashes: 0
- Routes: {'operating': 266, 'human_review': 41, 'not_city': 30, 'capital': 21, 'advice': 1, 'emergency': 1}
- Complaint type plausible for the city's own category: 192 of 281 scored ({'albuquerque': '26 of 44', 'chicago': '36 of 53', 'detroit': '36 of 53', 'houston': '36 of 49', 'new-haven': '31 of 47', 'richmond': '27 of 35'})
- Ordinary text flagged by the manipulation patterns: 0; by an AI agent: 1; blocked by the provider: 0
- NYC borough invented for a non-NYC complaint: 0; NYC district assigned: 12
- Escalated as possible emergencies: 1; urgent hazards (alert marked urgent, no 911): 66; held for danger words only: 1
- Personal information removed in: 2
- Median seconds per request: 2.3

Why requests were held:

- The agent had low confidence in its reading of the request.: 39
- The agent's answer can't be passed on as-is: describes people by characteristics (describe the condition inste: 1
- The agent flagged text that looks like instructions to the system.: 1
- The request uses words that can signal danger to life ("…"), but neither AI check found an emergency. A person: 1
