# CivicEquity test: results

- Date: 2026-10-25
- Tester: the builder, previously a budget analyst for the City of Boston (years: 4)

## Part A. Capital side (the budget analyst's job)

**A1. Memo.** Capital memo › Memo › generate.
- Seconds from clicking to a finished memo (the app shows it): about ten
- Would you send it as written? (yes / with edits / no): Potentially, I think the wording is unclear
- Edits you would make: I would expand on how I came to the recommendations
- Spot check: pick 3 numbers in the memo and find them in the appendix. How many matched?  3 of 3 (checked by Claude: 479,591 infrastructure requests, Queens Boulevard 366 complaints / $18.7M / 20 per $1M, and 5 Avenue 54 complaints / $411K / 131 per $1M all match the memo's facts and a fresh recount from the raw 311 data)

**A2. Before/after.** The closest comparable work you did in Boston (for example a capital-priorities brief or
a budget memo for leadership): I contributed analysis for budget memos
- What it was:
- How long it took (hours or days, including pulling data): These would take many hours of writing and countless hours of data analysis
- How many people were involved:

**A3. Equity dashboard.** Try all three "Compare by" options.
- Measures you would actually use: I really liked 
- Anything confusing or misleading:I don't think a lot of the specifics of the analysis were clear a methods section would possibly be helpful to explain what the analysis is
- Did Boston use anything like this? (what): We did analysis on capital spending to ensure that dollars were spent equitably across the city. We did not utilize 311 data as a way 

**A4. Cost estimate.** New request › sample "Recurring sewer backups (Brooklyn)".
- Is the planning range useful for early budgeting? (1–5): Yes, at this stage having even a rough estimate is extremely helpful.
- How you estimated costs at this stage in Boston:

## Part B. 311 side (quick check)

**B1. Five requests:** the samples "Pothole that keeps coming back", "Gas smell", "Found dog", and two of your own
from experience (no personal details).

| Request | Seconds in the app | Routed right? | Notes |
|---|---|---|---|
| | | | | stray dog/ about 3 seconds/ wish it would have advised to call animal control ,<- ik this is formatted wrong ill do the next ones right
| recurrign sewer backup| 5 seconds | routed right| no notes|
| natural disaster| 3 seconds| routed correctly | no notes|
| tree request| 9.8 seconds| routed right | no notes|
|asked for a new brooklyn bridge |Buffered for forever then AI checked issue| This was flagged as not a city issue| while this request is insane, it is indeed a city issue |

**B2. Approve or edit one alert** on Agency alerts. Seconds:  __3_  What you changed:

**B3. Emergency:** sample "Freezing apartment (Bronx)". Handled the way it should be? yes

## Part C. Your assessment

- Usefulness (1–5): _4__  Trust in the drafts (1–5): ___
- Most useful:The routing to a city agency (usually done by a person), the data cleaning and analyis, price estimates (this is something where guesses are very rough in the initial phase)
- What would stop a City office from using it: Privacy concers, the routing is not perfect and could filter out valid compaliants. While the recommendations are useful, there would be a lot of additional factors about whether they should be implemented.
- Anything wrong, unsafe, or unfair:
 I'm not entirely convinced the model would catch all safety concerns
- One thing to change: There is definite room for improvement in the routing algotrith.
- Two or three sentences in your own words for the report: While this is a rough prototype, the data analysis is extremely helpful. This was able to give a lot of information that we used to make decisions neatly and in a convincing format with an incredibly small fraction of the time.

## Bugs or problems (screenshots welcome)

- A request stayed on "AI triage" indefinitely (a crash after a page rerun mid-request). Fixed the same day: every AI call now runs on its own event loop.
- "Build another Brooklyn Bridge" was marked not a City matter. Fixed: City requests with no matching 311 type now go to a person.
- Found dog: no mention of animal control. Fixed: the reply now explains that Animal Care Centers of NYC runs the City's animal shelter system (verified source).
- Equity dashboard methods unclear. Fixed: "How to read this page" at the top.
- Memo did not explain how recommendations were chosen. Fixed: a code-written "How these were chosen" note.
