"""
Electron: weekly check for cheaper Spanish electricity tariffs vs the user's
current contract. Runs unattended via GitHub Actions (see
.github/workflows/electron.yml) so it does not depend on any local PC being on.

Approach: fixed scraping of a small set of known comparator/supplier pages.
This is intentionally brittle -- it only understands the page structures it
was written against, and will silently find nothing useful if a site changes
its layout. Check the Actions run logs if it stops finding offers.

Source page (iacompara.es) states power-term prices in EUR/kW/day, not
EUR/kW/month -- they get converted using AVG_DAYS_PER_MONTH before comparing
against the user's baseline, which is already in EUR/kW/month.
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

MIN_SAVINGS_THRESHOLD = 2.00  # EUR/month -- only alert above this

AVG_DAYS_PER_MONTH = 30.4368

BASELINE_COST = (POTENCIA_RATE * CONTRACTED_POWER) + (CONSUMPTION_RATE * ASSUMED_MONTHLY_KWH)

SOURCES = [
    "https://www.iacompara.es/blog/compania-electrica-mas-barata-2026",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ElectronTariffBot/1.0)"}

# "📊 Company Tariff Name:" block header
BLOCK_HEADER_RE = re.compile(r"^📊\s*(.+?):\s*$")
# "✅ Precio potencia punta/valle: X €/kW/día" or "✅ Precio potencia: X €/kW/día"
PRECIO_POTENCIA_RE = re.compile(
    r"Precio potencia(?:\s+(valle|punta))?:\s*(\d+[.,]\d+)\s*€/kW/d[ií]a", re.IGNORECASE
)
# "✅ Precio energía: X €/kWh"
PRECIO_ENERGIA_RE = re.compile(r"Precio energ[ií]a:\s*(\d+[.,]\d+)\s*€/kWh", re.IGNORECASE)
# "   • Potencia: X €/kW/día" or "   • Potencia valle: X €/kW/día | Punta: Y €/kW/día"
BULLET_POTENCIA_RE = re.compile(
    r"Potencia(?:\s+valle)?:\s*(\d+[.,]\d+)\s*€/kW/d[ií]a(?:\s*\|\s*Punta:\s*(\d+[.,]\d+)\s*€/kW/d[ií]a)?",
    re.IGNORECASE,
)
# "   • Energía: X €/kWh"
BULLET_ENERGIA_RE = re.compile(r"Energ[ií]a:\s*(\d+[.,]\d+)\s*€/kWh", re.IGNORECASE)
# Heading lines (h2/h3 text), used as a fallback name anchor
HEADING_NAME_RE = re.compile(r"^[🌟⚡🔄☀️🥇🥈🥉]*\s*([A-ZÀ-Ý][\w À-ÿ&\.\-]{2,50})$")


def to_float(s: str) -> float:
    return float(s.replace(",", "."))


def find_offers(html: str):
    """Walk the rendered text line by line, tracking the most recent block
    name (from a '📊 Name:' header or a heading), and pairing it with the
    next potencia (€/kW/día) and energia (€/kWh) values found before the
    next block starts. Returns a list of (name, potencia_eur_per_kw_day,
    consumo_eur_per_kwh)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]

    offers = []
    current_name = None
    current_potencia = None
    current_energia = None

    def flush():
        if current_name and current_potencia is not None and current_energia is not None:
            offers.append((current_name, current_potencia, current_energia))

    for line in lines:
        header_match = BLOCK_HEADER_RE.match(line)
        if header_match:
            flush()
            current_name = header_match.group(1).strip()
            current_potencia = None
            current_energia = None
            continue

        precio_potencia = PRECIO_POTENCIA_RE.search(line)
        if precio_potencia:
            period, value = precio_potencia.group(1), precio_potencia.group(2)
            rate = to_float(value)
            # Prefer the "punta" (peak) rate when both periods are reported
            # separately across lines, since that's the higher/more conservative figure.
            if period is None or period.lower() == "punta" or current_potencia is None:
                current_potencia = rate
            continue

        precio_energia = PRECIO_ENERGIA_RE.search(line)
        if precio_energia:
            current_energia = to_float(precio_energia.group(1))
            continue

        bullet_potencia = BULLET_POTENCIA_RE.search(line)
        if bullet_potencia:
            valle_val, punta_val = bullet_potencia.group(1), bullet_potencia.group(2)
            # Use punta (peak) if present, otherwise the single reported value.
            current_potencia = to_float(punta_val) if punta_val else to_float(valle_val)
            continue

        bullet_energia = BULLET_ENERGIA_RE.search(line)
        if bullet_energia:
            current_energia = to_float(bullet_energia.group(1))
            continue

        heading_match = HEADING_NAME_RE.match(line)
        if heading_match and current_potencia is None and current_energia is None:
            # A new heading with no data collected yet under the previous name:
            # treat it as a fresh anchor (covers Octopus's h3-per-tariff layout).
            current_name = heading_match.group(1).strip()

    flush()

    # Dedupe identical (name, potencia, energia) tuples -- the source page
    # repeats the same tariff's numbers in multiple sections.
    seen = set()
    deduped = []
    for offer in offers:
        if offer not in seen:
            seen.add(offer)
            deduped.append(offer)
    return deduped


def main():
    print(f"Baseline cost: {BASELINE_COST:.2f} EUR/month "
          f"({POTENCIA_RATE} EUR/kW * {CONTRACTED_POWER} kW + "
          f"{CONSUMPTION_RATE} EUR/kWh * {ASSUMED_MONTHLY_KWH} kWh)")

    best_company = None
    best_cost = None
    best_savings = -10**9

    for url in SOURCES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"Skipping {url}: fetch failed ({e})")
            continue

        offers = find_offers(resp.text)
        print(f"{url}: found {len(offers)} candidate offer(s)")

        for name, potencia_day_rate, kwh_rate in offers:
            potencia_month_rate = potencia_day_rate * AVG_DAYS_PER_MONTH
            cost = (potencia_month_rate * CONTRACTED_POWER) + (kwh_rate * ASSUMED_MONTHLY_KWH)
            savings = BASELINE_COST - cost
            print(f"  {name}: {potencia_day_rate} EUR/kW/day "
                  f"(~{potencia_month_rate:.2f} EUR/kW/mo), {kwh_rate} EUR/kWh -> "
                  f"~{cost:.2f} EUR/mo (savings: {savings:.2f} EUR/mo)")
            if savings > best_savings:
                best_savings = savings
                best_company = name
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
