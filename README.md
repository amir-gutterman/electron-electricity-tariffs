# Electron

Weekly check (Mondays ~9am Spain time) for cheaper residential electricity
tariffs in Spain vs. a fixed baseline tariff, run on GitHub Actions so it
works regardless of whether any local machine is on. Sends a WhatsApp message
via Twilio only when it finds an offer that saves more than 5 EUR/month.

## How it works

`electron.py` scrapes a small fixed list of comparator pages (see `SOURCES`),
extracts company name + price-per-kW + price-per-kWh using regex/heuristics,
computes an estimated monthly cost for a 4.5 kW / 500 kWh household, and
compares it to the baseline. This is a fixed-scraping approach (not an LLM),
so it is brittle: if a source site changes its page structure, extraction may
silently return nothing. Check the Actions run logs periodically.

## Required GitHub secrets

Set these in repo Settings → Secrets and variables → Actions:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_WHATSAPP_FROM` (e.g. `whatsapp:+14155238886`)
- `TWILIO_WHATSAPP_TO` (e.g. `whatsapp:+34626512763`)

## Manual run

Use the "Run workflow" button under Actions → Electron weekly tariff check,
or `gh workflow run electron.yml`.

## Editing the baseline tariff

Edit the constants at the top of `electron.py`:

- `POTENCIA_RATE`, `CONTRACTED_POWER`, `CONSUMPTION_RATE`, `ASSUMED_MONTHLY_KWH`
- `MIN_SAVINGS_THRESHOLD`
