# Other cities' 311 complaints (run1)

600 public complaints from 6 cities (SeeClickFix), resident wording, run through Triage and the Life-Safety Screener.

- Crashes: 0
- Routes: {'operating': 353, 'human_review': 94, 'not_city': 33, 'emergency': 84, 'capital': 34, 'advice': 2}
- Complaint type plausible for the city's own category: 335 of 481 scored ({'albuquerque': '54 of 81', 'chicago': '60 of 88', 'detroit': '67 of 97', 'houston': '66 of 82', 'new-haven': '54 of 78', 'richmond': '34 of 55'})
- Ordinary text flagged by the manipulation patterns: 0; by an AI agent: 3; blocked by the provider: 0
- NYC borough invented for a non-NYC complaint: 3; NYC district assigned: 29
- Escalated as possible emergencies: 84; held for danger words only: 6
- Personal information removed in: 1
- Median seconds per request: 2.3

Why requests were held:

- The agent had low confidence in its reading of the request.: 59
- Descriptor "…" is not listed for Dirty Condition; treated as unspecified.: 9
- Agent chose "…", which is not an official complaint type.: 7
- The request uses words that can signal danger to life ("…"), but neither AI check found an emergency. A person: 6
- Descriptor "…" is not listed for Litter Basket Complaint; treated as unspecified.: 2
- Descriptor "…" is not listed for Illegal Parking; treated as unspecified.: 2
- The Life-Safety Screener flagged text that looks like instructions to the system.: 1
- Descriptor "…" is not listed for Food Establishment; treated as unspecified.: 1
- The agent's answer can't be passed on as-is: numbers not in the request: 30, 6: 1
- Descriptor "…" is not listed for Water System; treated as unspecified.: 1
- Descriptor "…" is not listed for Street Sign - Damaged; treated as unspecified.: 1
- The agent's answer can't be passed on as-is: cross street is not a plain street name: 'STOCKTON STREET; DECATU: 1
