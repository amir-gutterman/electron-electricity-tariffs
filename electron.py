"""
Electron: weekly check for cheaper Spanish electricity tariffs vs the user's
current contract. Runs unattended via GitHub Actions (see
.github/workflows/electron.yml) so it does not depend on any local PC being on.

Approach: fixed scraping of a small set of known comparator/supplier pages.
This is intentionally brittle -- it only understands the page structures it
was written against, and will silently find nothing useful if a site changes
its layout. Check the Actions run logs if it stops finding offers.
"""

import os
import re
import sys

import requests
from bs4 import BeautifulSoup

# --- User's current tariff (before tax / IVA) ---
POTENCIA_RATE = 3.62        # EUR/kW/month
CONTRACTED_POWER = 4.5      # kW
CONSUMPTION_RATE = 0.098    # EUR/kWh
ASSUMED_MONTHLY_KWH = 500   # variable/approximate, used for comparison only

MIN_SAVINGS_THRESHOLD = 5.00  # EUR/month -- only alert above this

BASELINE_COST = (POTENCIA_RATE * CONTRACTED_POWER) + (CONSUMPTION_RATE * ASSUMED_MONTHLY_KWH)

# Pages known to publish per-kW and per-kWh rates in readable text near company names.
SOURCES = [
    "https://selectra.es/energia/comparador-luz",
    "https://www.iacompara.es/blog/compania-electrica-mas-barata-2026/",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ElectronTariffBot/1.0)"}

# Matches things like "0,098 €/kWh" or "0.098 EUR/kWh" and "3,62 €/kW" near a company name.
PRICE_KWH_RE = re.compile(r"(\d+[.,]\d+)\s*(?:€|EUR)\s*/\s*kWh", re.IGNORECASE)
PRICE_KW_RE = re.compile(r"(\d+[.,]\d+)\s*(?:€|EUR)\s*/\s*kW", re.IGNORECASE)


def to_float(s: str) -> float:
    return float(s.replace(",", "."))


def find_offers(html: str):
    """Best-effort extraction: look for a company-like heading followed within
    a short distance by both a kWh price and a kW price. Returns a list of
    (company, kw_rate, kwh_rate) tuples. Heuristic and brittle by design."""
    soup = BeautifulSoup(html, "html.parser")
    text_blocks = [el.get_text(" ", strip=True) for el in soup.find_all(["tr", "li", "div", "p"])]

    offers = []
    for block in text_blocks:
        kwh_match = PRICE_KWH_RE.search(block)
        kw_match = PRICE_KW_RE.search(block)
        if kwh_match and kw_match:
            # crude company name guess: first capitalized word sequence in the block
            name_match = re.match(r"\s*([A-ZÀ-Ý][\wÀ-ÿ&\.\- ]{2,40})", block)
            company = name_match.group(1).strip() if name_match else "Unknown supplier"
            offers.append((company, to_float(kw_match.group(1)), to_float(kwh_match.group(1))))
    return offers


def main():
    print(f"Baseline cost: {BASELINE_COST:.2f} EUR/month "
          f"({POTENCIA_RATE} EUR/kW * {CONTRACTED_POWER} kW + "
          f"{CONSUMPTION_RATE} EUR/kWh * {ASSUMED_MONTHLY_KWH} kWh)")

    best_company = None
    best_cost = None
    best_savings = -1

    for url in SOURCES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"Skipping {url}: fetch failed ({e})")
            continue

        offers = find_offers(resp.text)
        print(f"{url}: found {len(offers)} candidate offer(s)")

        for company, kw_rate, kwh_rate in offers:
            cost = (kw_rate * CONTRACTED_POWER) + (kwh_rate * ASSUMED_MONTHLY_KWH)
            savings = BASELINE_COST - cost
            print(f"  {company}: {kw_rate} EUR/kW, {kwh_rate} EUR/kWh -> "
                  f"~{cost:.2f} EUR/mo (savings: {savings:.2f} EUR/mo)")
            if savings > best_savings:
                best_savings = savings
                best_company = company
                best_cost = cost

    if best_company is None or best_savings <= MIN_SAVINGS_THRESHOLD:
        print(f"No offer found with savings > {MIN_SAVINGS_THRESHOLD} EUR/mo. No message sent.")
        return

    message = (
        f"⚡ Electron: {best_company} offers ~{best_cost:.0f} EUR/mo "
        f"vs your ~{BASELINE_COST:.0f} EUR/mo (save ~{best_savings:.0f} EUR/mo). Check their rates."
    )
    send_whatsapp(message)


def send_whatsapp(message: str):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_FROM"]
    to_number = os.environ["TWILIO_WHATSAPP_TO"]

    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data={"To": to_number, "From": from_number, "Body": message},
        auth=(account_sid, auth_token),
        timeout=20,
    )
    print(f"Twilio response: {resp.status_code} {resp.text}")
    if resp.status_code >= 300:
        sys.exit(1)


if __name__ == "__main__":
    main()
