# Sweepstakes Helper (Austria / AT) 🇦🇹

A modular, highly robust daily sweepstakes assistant and automated entry tool tailored specifically for users located in **Austria (AT)**.

It scrapes verified Austrian sweepstakes aggregators and global sweepstakes communities, filters strictly for Austria-eligible contests, deduplicates against an internal SQLite database, and executes submissions using a **two-tiered automation model** (headless automated submission with anti-bot stealth & interactive fallback with audio chime for CAPTCHAs).

---

## 🌟 Key Features

1. **Sources & Aggregation:**
   - **`gewinnspielsammlung.at` Scraper:** Extracts daily Austrian sweepstakes, sponsor names, prizes, exact expiry dates, quiz solutions (`Lösungsvorschlag`), and unrolls affiliate tracking links (Awin, Adcell, Tidd.ly).
   - **`r/sweepstakes` Scraper:** Extracts community giveaways with strict geo-filtering (keeps `[WW]`, `[EU]`, `[AT]`, `[Worldwide]` while rejecting `[US]`, `[CA]`, `[UK]`, `[AU]`, etc.). Includes automatic fallback to Playwright headless extraction if Reddit rate-limits or blocks direct HTTP.
2. **State Management & Deduplication:**
   - SQLite persistence tracking `id`, `source`, `external_id`, `url`, `title`, `deadline`, `eligibility_country`, `requires_manual_entry`, `status`, `quiz_answer`, and timestamps.
   - Intelligent deduplication: prevents duplicate entries while supporting **daily recurring contests** (re-queues once per calendar day).
3. **Anti-Bot & Stealth Infrastructure:**
   - Playwright with fingerprint evasion (`navigator.webdriver` removal, Chrome runtime spoofing, Austrian locale `de-AT`, Europe/Vienna timezone, randomized viewport, WebGL vendor mocks).
   - Randomized jitter delays (1.5s - 4.5s) and exponential backoff retry.
4. **Intelligent Form Matching & Compliance:**
   - Semantic German/English input matching (Vorname, Nachname, E-Mail, Telefon, Straße, Hausnummer, PLZ, Ort, Land = Österreich, Geburtsdatum, Anrede).
   - Automatic pre-filling of quiz questions from parsed listings.
   - **Checkbox Safety:** Automatically checks Age Verification (18+) and Terms & Conditions (AGB/Datenschutz), while **explicitly leaving optional promotional/marketing newsletters unchecked**.
5. **Tiered Execution Strategy:**
   - **Tier 1 (Fully Automated):** Headless stealth browser navigates, dismisses European GDPR cookie banners, fills fields, checks T&Cs, and submits cleanly.
   - **Tier 2 (Semi-Automated / Interactive Helper):** For contests with CAPTCHAs (Cloudflare Turnstile, reCAPTCHA, hCaptcha) or complex widgets, launches a headful browser window, pre-fills known fields, plays a terminal bell chime (`\a`), and prompts the user to solve the CAPTCHA or confirm submission.
6. **Alerts & Daily Notifications:**
   - Optional webhook notifications to **Discord** or **Telegram** summarizing daily discoveries, completed submissions, and items awaiting interactive assistance.
   - Rotating daily file logs (`logs/sweepstakes_YYYY-MM-DD.log`) via `loguru`.

---

## 📂 Project Architecture

```
Sweepstakes-Helper/
├── config/
│   ├── __init__.py
│   └── settings.py              # Pydantic-settings config (.env & user_profile.json)
├── database/
│   ├── __init__.py
│   └── db.py                    # SQLite state management & deduplication
├── models/
│   ├── __init__.py
│   └── sweepstake.py            # Pydantic models for Sweepstakes and UserProfile
├── scrapers/
│   ├── __init__.py
│   ├── base.py                  # Base scraper with backoff, jitter & UA rotation
│   ├── gewinnspielsammlung.py   # gewinnspielsammlung.at scraper
│   └── reddit.py                # r/sweepstakes scraper with AT geo-filter
├── automation/
│   ├── __init__.py
│   ├── browser.py               # Stealth Playwright launcher & evasions
│   ├── captcha_detector.py      # Cloudflare Turnstile, reCAPTCHA, hCaptcha detector
│   ├── form_filler.py           # Semantic German/English form filler & checkbox guard
│   └── runner.py                # Tiered execution controller
├── notifications/
│   ├── __init__.py
│   └── notifier.py              # Discord & Telegram daily report dispatchers
├── utils/
│   ├── __init__.py
│   └── logger.py                # Loguru daily rotating file + colored console sink
├── systemd/
│   ├── sweepstakes.service      # Systemd service unit template
│   └── sweepstakes.timer        # Systemd timer unit template (08:30 morning trigger)
├── tests/
│   └── test_sweepstakes.py      # Test suite
├── user_profile.json.example    # Profile template for Austrian resident details
├── .env.example                 # Environment variables template
├── main.py                      # Unified CLI batch runner & subcommands
├── requirements.txt
└── README.md
```

---

## 🚀 Installation

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.12 / 3.13)
- Chromium browser binaries (installed via Playwright)

### 2. Setup Virtual Environment
```bash
# Clone and enter workspace
git clone https://github.com/Cott3r/Sweepstakes-Helper.git
cd Sweepstakes-Helper

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

---

## ⚙️ Configuration

### 1. User Profile (`user_profile.json`)
Copy the example template and fill in your actual details:
```bash
cp user_profile.json.example user_profile.json
```

Edit `user_profile.json`:
```json
{
  "first_name": "Max",
  "last_name": "Mustermann",
  "email": "max.mustermann.gewinnspiele@gmail.com",
  "phone": "+436601234567",
  "street": "Mariahilfer Straße",
  "house_number": "101/2/5",
  "postal_code": "1060",
  "city": "Wien",
  "country": "Österreich",
  "birth_date": "15.06.1992",
  "salutation": "Herr"
}
```

### 2. Environment Variables (`.env`)
Copy `.env.example`:
```bash
cp .env.example .env
```
Key configuration settings:
- `HEADLESS=true`: Run browser in background for automated submissions.
- `INTERACTIVE_FALLBACK=true`: Automatically prompt for manual CAPTCHA solving when running interactively.
- `PLAY_AUDIO_CHIME=true`: Ring terminal bell when user interaction is required.
- `DISCORD_WEBHOOK_URL=`: (Optional) Paste webhook URL to receive daily summary reports.
- `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=`: (Optional) Telegram bot alerts.

---

## 💻 CLI Usage

The script is driven through a unified CLI interface:

### 1. Full Daily Run (Scrape + Deduplicate + Automated Entries + Summary)
```bash
python main.py run
```
*Note: In automated batch / cron runs, append `--no-interactive` so it runs completely unattended and flags CAPTCHAs for later review.*
```bash
python main.py run --no-interactive
```

### 2. Scrape Only
Fetch all new listings, filter for Austrian eligibility, deduplicate against the database, and display discovery counts:
```bash
python main.py scrape
```

### 3. Entry Processing Only
Process pending automated entries without scraping:
```bash
python main.py enter --limit 20
```

### 4. Interactive Helper for Manual Entries
Open a headful browser session for sweepstakes flagged with CAPTCHAs or custom actions. Pre-fills recognized fields, plays an alert chime, and waits for your confirmation:
```bash
python main.py manual
```

### 5. Check Database Statistics
```bash
python main.py status
```
Output:
```
============================================================
  SWEEPSTAKES HELPER (AUSTRIA) - DATABASE STATUS
============================================================
Total Sweepstakes:         80
Completed:                 1
Pending (Automated):       69
Pending (Manual Required): 10
Skipped:                   0
Failed:                    0
Daily Recurring Tracks:    3
------------------------------------------------------------
Today Discovered:          80
Today Completed:           1
============================================================
```

### 6. Test Stealth Browser
Validates that bot detection mitigations (`navigator.webdriver` removal, Austrian locale, viewport) are functioning:
```bash
python main.py test-browser
```

---

## ⏰ Daily Automation Setup

### Option A: Standard Cron (Recommended for desktop / server)
Run every morning at 08:30 (Europe/Vienna timezone):

```bash
crontab -e
```

Add the following entry:
```cron
# Daily sweepstakes run at 08:30 CET
30 08 * * * cd /home/<username>/Documents/PycharmProjects/Sweepstakes-Helper && /home/<username>/Documents/PycharmProjects/Sweepstakes-Helper/.venv/bin/python main.py run --no-interactive >> logs/cron.log 2>&1
```

### Option B: Systemd Timer (Recommended for modern Linux)
Templates are located in `systemd/`:

1. Copy service and timer to systemd user directory:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp systemd/sweepstakes.service ~/.config/systemd/user/
   cp systemd/sweepstakes.timer ~/.config/systemd/user/
   ```
2. Enable and start the timer:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now sweepstakes.timer
   ```
3. Check timer status:
   ```bash
   systemctl --user list-timers
   ```

---

## 🛡️ Anti-Bot Mitigation Strategy

| Challenge | Mitigation Implemented |
|---|---|
| **TLS & Header Fingerprinting** | Realistic modern Chrome & Firefox headers, `de-AT` language header, `DNT=1` |
| **Pacing / Rate-Limiting** | Exponential backoff (factor 1.8), randomized request jitter (1.5s - 4.5s) |
| **Headless Detection** | `navigator.webdriver` overwritten to `undefined`, Chrome runtime object mocked, fake WebGL vendor and plugins |
| **Geo-Blocking** | Direct emulation of Austrian geolocation (Vienna coordinates 48.2082, 16.3738) and `Europe/Vienna` timezone |
| **Reddit 403 / 429 Scraping Block** | Dual scraping mechanism: attempts HTTP API first, transparently falls back to Playwright DOM extraction |
| **Cloudflare / reCAPTCHA / hCaptcha** | Automated detection without crashes; flags item and seamlessly hands off to headful Interactive Helper |
| **Unsolicited Newsletters** | Strict inspection: terms & age verification are checked, optional marketing checkboxes are explicitly left unchecked |

---

## 🧪 Testing

Run the automated test suite:
```bash
PYTHONPATH=. .venv/bin/pytest tests/
```
All unit and integration tests pass verifying geo-filtering, semantic matching, SQLite deduplication, and daily recurring re-queueing.
