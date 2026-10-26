# CivicEquity user test: session guide

About 25 minutes. You run the session; the tester uses the app. Fill in `results.md` as you go.

## Before the tester arrives (10 minutes)

1. **Restart the app** so it runs the latest code:
   ```
   pkill -f "streamlit run app.py"
   cd "/Users/maggiefriel/Desktop/AI Foundations/Final Project"
   source .venv/bin/activate
   streamlit run app.py
   ```
   Open http://localhost:8501 and analyze one sample request to make sure the AI answers.
2. **How they'll use it.** In person on your laptop is easiest. Remotely, share your screen on Zoom and give
   them remote control, or read their choices aloud and click for them. Don't put the app on the internet.
3. **A clean queue (optional).** Ask Claude to back up and clear the test runs in `logs/`, so the Agency alerts
   page shows only the tester's work.
4. **Have ready:** a stopwatch (phone), `results.md` open, and the request list below.
5. **No real resident information.** If the tester wants to try a real case from their own experience, ask them
   to leave out names, phone numbers, and exact addresses.

## Opening (2 minutes): read this

> "This is CivicEquity, a class project that sorts 311 requests, drafts alerts to City agencies for a person to
> approve, and writes a capital-planning memo. Nothing is sent anywhere; it's a simulation. I'm testing the
> app, not you. Please think out loud: say what you expect, what surprises you, and what you'd do at work.
> May I quote you in my class report? If so, by name and title, by title only, or anonymously?"

Write their answer about quoting in `results.md`.

## Tasks (15 minutes)

Give one task at a time. Don't explain the screen unless they're stuck for more than a minute; note where they
got stuck. Time each task.

1. **Sort a routine request.** On **New request**, paste:
   *"There's a huge pothole on Atlantic Avenue at Nostrand in Brooklyn, two cars blew tires this morning."*
   Ask: "Where does this go, and would you send the alert as written?"
2. **Approve or edit the alert.** Have them edit anything they'd change, then approve it with their name.
3. **An emergency.** Paste: *"My apartment on Grand Concourse is -7 degrees inside, heat off two days, I have a
   newborn."* Ask: "Is this handled the way it should be? Who else should be told?"
4. **Their own case.** Ask them to type a request they've seen in real life (no personal details). Ask: "Did it
   get this right?"
5. **The memo and equity view.** Open **Capital memo › Memo** (generate one, about a minute) and **Equity
   dashboard**. Ask: "Would this help decide where capital money goes? What's missing?"

## Measure (during the tasks)

For tasks 1, 3, and 4, record:
- **Time in the app:** seconds from pasting the request to deciding to approve.
- **Time today:** ask, "How long would sorting and routing this take you or your office today?"
- **Right or wrong:** was the type, route, or agency right in their opinion?

## Closing questions (5 minutes)

Ask these, and write down their exact words for any answer you might quote:
1. "What was most useful?"
2. "What would stop you from using it at work?"
3. "Did anything feel wrong, unsafe, or unfair?"
4. "On a scale of 1 to 5, how useful would this be? How much would you trust its drafts?"
5. "If you could change one thing, what would it be?"

Thank them. Afterward, give Claude `results.md` to fold into the report and to fix anything that broke.
