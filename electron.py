"""
Electron: weekly check for cheaper Spanish electricity tariffs vs the user's
current contract. Runs unattended via GitHub Actions (see
.github/workflows/electron.yml) so it does not depend on any local PC being on.

Approach: fixed scraping. Two tiers of source, by trust level:

- OFFICIAL parsers read each supplier's own tariff page directly. These are
  trusted enough to trigger an alert on their own.
- The AGGREGATOR parser reads a third-party comparison blog (iacompara.es).
  Aggregator content is unverified (it may be biased, outdated, or simply
  wrong) and is logged for visibility but never triggers an alert by itself.

All of this is intentionally brittle -- each parser only understands the
exact page structure it was written against, and will silently find nothing
if a site redesigns. Check the Actions run logs if it stops finding offers.
"""

import os
import re
import smtplib
from email.message import EmailMessage

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

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ElectronTariffBot/1.0)"}


class Offer:
    def __init__(self, company, potencia_eur_per_kw_month, kwh_rate, source_url, trusted, note=""):
        self.company = company
        self.potencia_eur_per_kw_month = potencia_eur_per_kw_month
        self.kwh_rate = kwh_rate
        self.source_url = source_url
        self.trusted = trusted  # official source vs third-party aggregator
        self.note = note
        self.cost = (potencia_eur_per_kw_month * CONTRACTED_POWER) + (kwh_rate * ASSUMED_MONTHLY_KWH)
        self.savings = BASELINE_COST - self.cost

    def __str__(self):
        tag = "OFFICIAL" if self.trusted else "AGGREGATOR (unverified)"
        return (f"[{tag}] {self.company}: {self.potencia_eur_per_kw_month:.2f} EUR/kW/mo, "
                f"{self.kwh_rate:.4f} EUR/kWh -> ~{self.cost:.2f} EUR/mo "
                f"(savings: {self.savings:.2f} EUR/mo){' - ' + self.note if self.note else ''}")


def to_float(s: str) -> float:
    return float(s.replace(",", "."))


# --- OFFICIAL parser: Endesa "Tarifa Luz Fija 24h Online" ---
def parse_endesa():
    url = "https://www.endesa.com/es/luz-y-gas/luz/one/tarifa-one-luz"
    resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Each price is rendered as: aria-label="Precio actual  X" (current/promo price)
    # and optionally aria-label="Precio inicial  Y" (standard price after any promo).
    # The page lists energy price first, then the flat power-term price.
    actual_matches = re.findall(r'aria-label="Precio actual\s+([\d,]+)"', html)
    inicial_matches = re.findall(r'aria-label="Precio inicial\s+([\d,]+)"', html)

    if len(actual_matches) < 2:
        raise ValueError(f"Expected at least 2 'Precio actual' values, found {len(actual_matches)}")

    # First pair is the energy price (promo + standard if discounted);
    # use the standard (non-promotional) rate for an honest long-term comparison.
    kwh_rate = to_float(inicial_matches[0]) if inicial_matches else to_float(actual_matches[0])
    # Power term has no promo in this tariff -- flat monthly rate, already EUR/kW/month.
    potencia_month_rate = to_float(actual_matches[1])

    return Offer(
        company="Endesa (Tarifa Luz Fija 24h Online)",
        potencia_eur_per_kw_month=potencia_month_rate,
        kwh_rate=kwh_rate,
        source_url=url,
        trusted=True,
        note="using standard post-promo energy rate",
    )


# --- OFFICIAL parser: Plenitude "Fácil Luz" ---
def parse_plenitude():
    url = "https://eniplenitude.es/hogar/tarifas-luz/facil/"
    resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Energy price table: rows of "Sin impuestos" / "Con impuestos" with
    # columns for power bands (<=5kW, P1-P2 / P1-P2-P3). Take the "Sin
    # impuestos" row, "Hasta 5kW" column, matching our 4.5kW contracted power.
    m = re.search(r"Sin impuestos</td>\s*<td[^>]*>([\d,]+)</td>", html)
    if not m:
        raise ValueError("Could not find 'Sin impuestos' energy price row")
    kwh_rate = to_float(m.group(1))

    # Plenitude's marketing page does not publish its own power-term (potencia)
    # rate -- it appears to pass through the regulated/access rate rather than
    # competing on it. Without a published number, assume it matches the
    # user's current potencia rate so the comparison isn't skewed in Plenitude's
    # favor by a missing figure.
    potencia_month_rate = POTENCIA_RATE

    return Offer(
        company="Plenitude (Fácil Luz)",
        potencia_eur_per_kw_month=potencia_month_rate,
        kwh_rate=kwh_rate,
        source_url=url,
        trusted=True,
        note="potencia not published by supplier; assumed equal to current rate",
    )


OFFICIAL_PARSERS = [parse_endesa, parse_plenitude]


# --- AGGREGATOR parser: iacompara.es blog (unverified third-party source) ---
AGGREGATOR_URL = "https://www.iacompara.es/blog/compania-electrica-mas-barata-2026"

BLOCK_HEADER_RE = re.compile(r"^📊\s*(.+?):\s*$")
PRECIO_POTENCIA_RE = re.compile(
    r"Precio potencia(?:\s+(valle|punta))?:\s*(\d+[.,]\d+)\s*€/kW/d[ií]a", re.IGNORECASE
)
PRECIO_ENERGIA_RE = re.compile(r"Precio energ[ií]a:\s*(\d+[.,]\d+)\s*€/kWh", re.IGNORECASE)
BULLET_POTENCIA_RE = re.compile(
    r"Potencia(?:\s+valle)?:\s*(\d+[.,]\d+)\s*€/kW/d[ií]a(?:\s*\|\s*Punta:\s*(\d+[.,]\d+)\s*€/kW/d[ií]a)?",
    re.IGNORECASE,
)
BULLET_ENERGIA_RE = re.compile(r"Energ[ií]a:\s*(\d+[.,]\d+)\s*€/kWh", re.IGNORECASE)
HEADING_NAME_RE = re.compile(r"^[🌟⚡🔄☀️🥇🥈🥉]*\s*([A-ZÀ-Ý][\w À-ÿ&\.\-]{2,50})$")


def parse_aggregator():
    resp = requests.get(AGGREGATOR_URL, headers=HEADERS, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]

    offers = []
    current_name = None
    current_potencia_day = None
    current_energia = None

    def flush():
        if current_name and current_potencia_day is not None and current_energia is not None:
            offers.append((current_name, current_potencia_day, current_energia))

    for line in lines:
        header_match = BLOCK_HEADER_RE.match(line)
        if header_match:
            flush()
            current_name = header_match.group(1).strip()
            current_potencia_day = None
            current_energia = None
            continue

        precio_potencia = PRECIO_POTENCIA_RE.search(line)
        if precio_potencia:
            period, value = precio_potencia.group(1), precio_potencia.group(2)
            rate = to_float(value)
            if period is None or period.lower() == "punta" or current_potencia_day is None:
                current_potencia_day = rate
            continue

        precio_energia = PRECIO_ENERGIA_RE.search(line)
        if precio_energia:
            current_energia = to_float(precio_energia.group(1))
            continue

        bullet_potencia = BULLET_POTENCIA_RE.search(line)
        if bullet_potencia:
            valle_val, punta_val = bullet_potencia.group(1), bullet_potencia.group(2)
            current_potencia_day = to_float(punta_val) if punta_val else to_float(valle_val)
            continue

        bullet_energia = BULLET_ENERGIA_RE.search(line)
        if bullet_energia:
            current_energia = to_float(bullet_energia.group(1))
            continue

        heading_match = HEADING_NAME_RE.match(line)
        if heading_match and current_potencia_day is None and current_energia is None:
            current_name = heading_match.group(1).strip()

    flush()

    seen = set()
    result = []
    for name, potencia_day, kwh in offers:
        key = (name, potencia_day, kwh)
        if key in seen:
            continue
        seen.add(key)
        result.append(Offer(
            company=name,
            potencia_eur_per_kw_month=potencia_day * AVG_DAYS_PER_MONTH,
            kwh_rate=kwh,
            source_url=AGGREGATOR_URL,
            trusted=False,
        ))
    return result


def main():
    print(f"Baseline cost: {BASELINE_COST:.2f} EUR/month "
          f"({POTENCIA_RATE} EUR/kW * {CONTRACTED_POWER} kW + "
          f"{CONSUMPTION_RATE} EUR/kWh * {ASSUMED_MONTHLY_KWH} kWh)")

    all_offers = []

    for parser in OFFICIAL_PARSERS:
        try:
            offer = parser()
            all_offers.append(offer)
            print(f"  {offer}")
        except Exception as e:
            print(f"Skipping official parser {parser.__name__}: failed ({e})")

    try:
        aggregator_offers = parse_aggregator()
        print(f"{AGGREGATOR_URL}: found {len(aggregator_offers)} aggregator offer(s) (unverified)")
        for offer in aggregator_offers:
            all_offers.append(offer)
            print(f"  {offer}")
    except Exception as e:
        print(f"Skipping aggregator source: failed ({e})")

    # Only OFFICIAL offers can trigger an alert -- aggregator data is logged
    # above for visibility but is not trustworthy enough to act on alone.
    trusted_offers = [o for o in all_offers if o.trusted]
    best = max(trusted_offers, key=lambda o: o.savings, default=None)

    if best is None or best.savings <= MIN_SAVINGS_THRESHOLD:
        print(f"No official offer found with savings > {MIN_SAVINGS_THRESHOLD} EUR/mo. No message sent.")
        return

    subject = f"Electron: cheaper tariff found - {best.company}"
    body = (
        f"{best.company} offers an estimated ~{best.cost:.2f} EUR/month "
        f"vs your current ~{BASELINE_COST:.2f} EUR/month baseline "
        f"(save ~{best.savings:.2f} EUR/month).\n\n"
        f"Source (official supplier page): {best.source_url}\n"
        + (f"Note: {best.note}\n" if best.note else "")
        + "\nThis is an automated estimate based on scraped tariff data - verify directly "
        "with the supplier before switching."
    )
    send_email(subject, body)


def send_email(subject: str, body: str):
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_address = os.environ.get("ALERT_EMAIL_TO", gmail_address)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = to_address
    msg.set_content(body)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(gmail_address, gmail_app_password)
        smtp.send_message(msg)
    print(f"Email sent to {to_address}")


if __name__ == "__main__":
    main()
