# Turnover Clean Quote Bot for Slack

A conversational Slack bot that walks your team through building Airbnb turnover cleaning quotes step by step in a thread. Pricing auto-adjusts based on zip code market tier, with support for add-on services and volume discounts.

## What It Does

Type `/quote` in any Slack channel and the bot starts a thread:

```
Bot:  👋 Let's build a quote! What's the partner or client name?
You:  StayWell Properties
Bot:  Got it. What's the property zip code?
You:  95624
Bot:  📍 95624 → Moderate Cost Market (Tier 2). Rates adjusted.
Bot:  🏠 Unit type #1 — What's the apartment size? (e.g., 2/1)
You:  2/2
Bot:  2BR / 2BA — How many units of this type?
You:  8
Bot:  Any add-on services? (deep / laundry / both / none)
You:  both
Bot:  ✅ Added 8× 2BR / 2BA at $234/unit (payout: $175, margin: 25.2%)
      Type "add" for another type or "done" to finalize.
You:  done
Bot:  🎉 Portfolio qualifies for volume discount. Enter 0–15%.
You:  10
Bot:  [Full formatted quote summary with breakdown]
```

## Features

- **Market-tier pricing** — zip code auto-detects cost tier (1–4), adjusts all rates
- **Add-on services** — Deep clean and laundry with per-market pricing
- **Volume discounts** — 0–15% for 10+ unit portfolios
- **Margin protection** — warns if discount pushes margin below 25%
- **Internal cost view** — shows payout, supply, supervisor costs (stays in thread, not on client docs)
- **Team-friendly** — any team member can run `/quote`

## Setup Guide

### Step 1: Create the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**
2. Choose **From scratch**, name it "Quote Bot", select your workspace

### Step 2: Configure Permissions

1. Go to **OAuth & Permissions** in the sidebar
2. Under **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `commands` — slash commands
   - `app_mentions:read` — (optional) respond to @mentions
3. Click **Install to Workspace** and authorize
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### Step 3: Enable Socket Mode

Socket Mode lets the bot run without a public URL (perfect for Render free tier).

1. Go to **Socket Mode** in the sidebar and toggle it **on**
2. Create an app-level token with the `connections:write` scope
3. Copy the **App-Level Token** (starts with `xapp-`)

### Step 4: Create the Slash Command

1. Go to **Slash Commands** in the sidebar
2. Click **Create New Command**:
   - Command: `/quote`
   - Short description: `Start a cleaning quote`
   - Usage hint: `(starts a threaded conversation)`
3. Save

### Step 5: Subscribe to Events

1. Go to **Event Subscriptions** and toggle **on**
2. Under **Subscribe to bot events**, add:
   - `message.channels`
   - `message.groups`
   - `message.im`
   - `app_home_opened` (optional)
3. Save

### Step 6: Reinstall the App

After adding scopes/events, go to **Install App** and click **Reinstall to Workspace**.

### Step 7: Set Environment Variables

Create a `.env` file (or set these in your hosting platform):

```
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
```

### Step 8: Run Locally (Test First)

```bash
# Clone or download the project
cd slack-quote-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...

# Run
python app.py
```

Go to Slack and type `/quote` in any channel the bot is in.

## Deploy to Render (Free Tier)

1. Push the project to a GitHub repo
2. Go to [render.com](https://render.com) → **New** → **Background Worker**
3. Connect your GitHub repo
4. Settings:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `python app.py`
5. Add environment variables:
   - `SLACK_BOT_TOKEN` → your xoxb- token
   - `SLACK_APP_TOKEN` → your xapp- token
6. Deploy

Render's free tier spins down after inactivity but restarts on the next `/quote` command (may take ~30 seconds on first use after idle).

## Project Structure

```
slack-quote-bot/
├── app.py              # Main bot logic + conversation flow
├── pricing.py          # Pricing engine (market tiers, rates, calculations)
├── requirements.txt    # Python dependencies
├── Procfile            # Render deploy config
├── .env.example        # Environment variable template
└── README.md           # This file
```

## Customization

### Adjust Market Rates

Edit the `get_base_rates()` and `get_addon_rates()` functions in `pricing.py` to change payout rates per tier.

### Add More Zip Code Tiers

Add zip code prefixes to `ZIP_HIGH4`, `ZIP_HIGH3`, or `ZIP_MODERATE` lists in `pricing.py`.

### Change Supply Costs

Update the `SUPPLY_COSTS` dict in `pricing.py`.

### Change Margin Threshold

Update `MIN_MARGIN` in `pricing.py` (default: 0.25 = 25%).

### Change Supervisor Overhead

Update `SUPERVISOR_OVERHEAD_PCT` in `pricing.py` (default: 0.12 = 12%).

## Commands During a Quote

| Command | What it does |
|---------|-------------|
| `cancel` | Cancels the current quote |
| `restart` | Starts over from scratch |
| `add` | Add another unit type |
| `done` | Finalize and move to discount/review |
| `edit` | Go back and redo unit configuration |
| `confirm` | Save the final quote |

## Notes

- Sessions are stored in memory. If the bot restarts, active sessions are lost (completed quotes are already posted to the thread).
- For production with multiple workers, swap the in-memory `sessions` dict for Redis.
- The bot must be invited to a channel to respond there (or use DMs).
