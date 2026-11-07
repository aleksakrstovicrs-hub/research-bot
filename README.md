# Research Bot — AI lead finder for web agencies

A Telegram-controlled system that finds local businesses in Serbia, audits their websites and drafts personalized outreach emails. One command (`/trazi dentists Belgrade`) runs the whole pipeline on a Linux VPS and returns a ranked list of businesses that most likely need a new website.

> The code, comments and bot messages are in Serbian, as the system was built for the Serbian market.

## How it works

```
/trazi <niche> <city>
   │
   ├─ 1. Discover   businesses from OpenStreetMap (Nominatim + Overpass), public institutions filtered out
   ├─ 2. Analyze    each site in headless Chromium: status, SSL, mobile layout, outdated tech, screenshots;
   │                if a business has no known site, guess its domain and confirm it by phone or name + address
   ├─ 3. Emails     find the public business email on the site (respecting robots.txt), verify MX records
   ├─ 4. Speed      Google PageSpeed Insights mobile score and Core Web Vitals
   ├─ 5. Design     an OpenAI vision model rates the design from desktop and mobile screenshots
   └─ 6. Score      every lead gets an opportunity score (0–100) and a category, synced to Google Sheets
```

**Outreach (phase 2):** the AI writes a short email for each lead based on its category (no site, broken site, no SSL, not mobile-friendly, outdated, slow, good site). Every draft is sent to Telegram with **Send / Edit / Skip** buttons — nothing is sent without approval. Sending respects a daily limit, working hours, gaps between emails, unsubscribe requests and a single follow-up.

**Client portal:** a second Telegram bot lets paying clients (e.g. web studios) request their own Excel reports with screenshots and ready-to-edit pitches, using invite codes and a monthly limit per client. Reports use a separate database per client.

## Features

- Fully asynchronous pipeline with configurable concurrency
- Website discovery with verification, so a business is never matched to someone else's site
- Opportunity scoring that combines technical checks, speed and AI design review
- Google Sheets as a two-way UI: the owner edits status and notes, opt-outs are honored on every sync
- GDPR-minded: only publicly listed business emails, unsubscribe header, opt-out list
- Shared file lock so the research bot and the client portal never run heavy jobs at the same time
- Health check script (`zdravlje.py`) for every service and API
- Runs as `systemd` services with memory limits on a small VPS

## Tech

Python 3.12 · asyncio · python-telegram-bot · Playwright (Chromium) · OpenAI API (vision + structured outputs) · Google Sheets API (gspread) · Google PageSpeed Insights · OpenStreetMap Overpass/Nominatim · SQLite · httpx · dnspython · openpyxl · systemd

## Project structure

```
bot.py              Telegram bot for the owner (search, status, drafts, approvals, sending)
portal_bot.py       Telegram bot for clients (invite codes, monthly limits, report queue)
pipeline.py         Runs steps 1–6 in sequence
discover.py         Step 1 — find businesses
analyze.py          Step 2 — website checks and screenshots
find_emails.py      Step 3 — public business emails
pagespeed.py        Step 4 — mobile speed
design_review.py    Step 5 — AI design review
status.py           Step 6 — categories and report
izvestaj.py         Excel report for a client
provera.py          Checks that all API keys work
zdravlje.py         Health check of the whole system
klijenti/           Core library: database, checks, scoring, email drafting, mailer, Sheets sync, OSM
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
cp env.example .env        # fill in your own keys
python provera.py          # verify the keys
python bot.py
```

You need your own Telegram bot token, OpenAI API key, Google Cloud API key (PageSpeed) and a Google service account with access to a sheet. The `.service` files show how the bots run under `systemd`.

## Author

Aleksa Krstović — [aleksakrstovic.rs](https://aleksakrstovic.rs)
