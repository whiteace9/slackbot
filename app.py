"""
Slack Quote Bot — Conversational Airbnb turnover cleaning quote builder.

Slash command: /quote
Walks users through building a quote step-by-step in a thread.
"""

import os
import re
import json
import math
import logging
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from pricing import (
    get_market_tier, get_base_rates, get_addon_rates,
    calculate_unit_price, calculate_quote,
    TIER_LABELS, UNIT_LABELS, VALID_CONFIGS,
)

# ── Setup ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# In-memory session store (keyed by thread_ts)
# For production with multiple workers, swap for Redis
sessions = {}

# ── Session Management ──────────────────────────────────

def get_session(thread_ts):
    return sessions.get(thread_ts)

def set_session(thread_ts, data):
    sessions[thread_ts] = data

def clear_session(thread_ts):
    sessions.pop(thread_ts, None)

# ── Formatting Helpers ──────────────────────────────────

def format_currency(amount):
    return f"${amount:,.2f}" if isinstance(amount, float) else f"${amount:,}"

def build_quote_summary_blocks(quote, session):
    """Build rich Slack blocks for the final quote summary."""
    q = quote
    s = session

    header_text = f":sparkles: *Quote Ready* — {s.get('partner', 'Partner')}"
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Turnover Cleaning Quote", "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Partner:*\n{s.get('partner', '—')}"},
            {"type": "mrkdwn", "text": f"*Market:*\n{s['zip']} · {TIER_LABELS[s['tier']]}"},
            {"type": "mrkdwn", "text": f"*Total Units:*\n{q['total_units']}"},
            {"type": "mrkdwn", "text": f"*Date:*\n{datetime.now().strftime('%B %d, %Y')}"},
        ]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Unit Pricing Breakdown*"}},
    ]

    # Line items
    for item in q["line_items"]:
        addons_str = ", ".join(item["addons"]) if item["addons"] else "Standard only"
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{item['label']}* × {item['qty']}"},
                {"type": "mrkdwn", "text": f"Quote: *{format_currency(item['unit_price'])}*/unit → *{format_currency(item['line_total'])}*"},
                {"type": "mrkdwn", "text": f"Payout: {format_currency(item['cleaner_total'])} (range: ${item['payout_range'][0]}–${item['payout_range'][1]})"},
                {"type": "mrkdwn", "text": f"Add-ons: {addons_str} | Margin: {item['margin']}%"},
            ],
        })

    blocks.append({"type": "divider"})

    # Totals
    totals_lines = [f"*Subtotal:* {format_currency(q['subtotal'])}"]
    if q["discount_pct"] > 0:
        totals_lines.append(f"*Volume Discount ({q['discount_pct']}%):* -{format_currency(q['discount_amt'])}")
    totals_lines.append(f"\n:moneybag: *Total Per Clean Cycle: {format_currency(q['final_total'])}*")
    totals_lines.append(f"Lowest margin after discount: *{q['worst_margin']}%* {'✅' if q['margin_ok'] else '⚠️ Below 25% target'}")

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(totals_lines)},
    })

    # Internal cost note (visible in thread, not on PDF)
    cost_lines = ["_Internal cost breakdown (not shared with partner):_"]
    for item in q["line_items"]:
        cost_lines.append(
            f"• {item['label']}: Cost ${item['total_cost']:.2f} → "
            f"Price {format_currency(item['unit_price'])} → "
            f"Profit {format_currency(item['unit_price'] - item['total_cost'])}/unit"
        )
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "\n".join(cost_lines)}],
    })

    return blocks


# ── Conversation Steps ──────────────────────────────────
# Each step: prompt the user, then handle their reply to advance

STEPS = [
    "ask_partner",
    "ask_zip",
    "ask_unit_config",    # beds/baths
    "ask_unit_qty",       # how many of this type
    "ask_addons",         # deep clean / laundry
    "ask_more_units",     # add another unit type or continue
    "ask_discount",       # volume discount
    "confirm",            # review and confirm
]


def start_session(thread_ts, channel, user_id):
    """Initialize a new quote session."""
    set_session(thread_ts, {
        "channel": channel,
        "user_id": user_id,
        "step": "ask_partner",
        "partner": None,
        "zip": None,
        "tier": None,
        "units": [],         # list of {beds, baths, qty, deep_clean, laundry, pricing}
        "current_unit": {},  # unit being configured
        "discount": 0,
    })


def send_step_prompt(client, channel, thread_ts, session):
    """Send the prompt for the current step."""
    step = session["step"]

    if step == "ask_partner":
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=":wave: Let's build a quote! What's the *partner or client name*?",
        )

    elif step == "ask_zip":
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"Got it — quoting for *{session['partner']}*.\n\nWhat's the *property zip code*?",
        )

    elif step == "ask_unit_config":
        tier = session["tier"]
        rates = get_base_rates(tier)
        unit_num = len(session["units"]) + 1

        rate_preview = "\n".join([
            f"  • {UNIT_LABELS[k]}: ${r[0]}–${r[2]} (mid: ${r[1]})"
            for k, r in list(rates.items())[:6]
        ])

        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=(
                f":house: *Unit type #{unit_num}* — What's the apartment size?\n\n"
                f"Type it as `beds/baths` (e.g., `2/1` for 2BR/1BA, or `0/1` for a studio).\n\n"
                f"Available configs: {', '.join(UNIT_LABELS[k] for k in VALID_CONFIGS)}\n\n"
                f"_Suggested payout ranges for {TIER_LABELS[tier]}:_\n{rate_preview}"
            ),
        )

    elif step == "ask_unit_qty":
        cu = session["current_unit"]
        label = UNIT_LABELS.get(f"{cu['beds']}-{cu['baths']}", f"{cu['beds']}BR/{cu['baths']}BA")
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"*{label}* — How many units of this type?",
        )

    elif step == "ask_addons":
        addons = get_addon_rates(session["tier"])
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=(
                f"Any add-on services for this unit type?\n\n"
                f"  • `deep` — {addons['deep_clean']['label']} (+${addons['deep_clean']['rate']})\n"
                f"  • `laundry` — {addons['laundry']['label']} (+${addons['laundry']['rate']})\n"
                f"  • `both` — Include both add-ons\n"
                f"  • `none` — Standard turnover only"
            ),
        )

    elif step == "ask_more_units":
        total_so_far = sum(u["qty"] for u in session["units"])
        last = session["units"][-1]
        p = last["pricing"]
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=(
                f":white_check_mark: Added *{last['qty']}× {p['label']}* at *{format_currency(p['unit_price'])}/unit*"
                f" (payout: ${p['cleaner_total']:.0f}, margin: {p['margin']}%)\n\n"
                f"Running total: *{total_so_far} units* across {len(session['units'])} type(s).\n\n"
                f"Type `add` to configure another unit type, or `done` to finalize the quote."
            ),
        )

    elif step == "ask_discount":
        total_units = sum(u["qty"] for u in session["units"])
        if total_units >= 10:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=(
                    f":tada: This portfolio qualifies for a volume discount ({total_units} units).\n\n"
                    f"Enter a discount percentage (0–15), or type `0` for no discount.\n"
                    f"Quick options: `5`, `10`, or `15`"
                ),
            )
        else:
            # Skip discount for small portfolios
            session["step"] = "confirm"
            set_session(thread_ts, session)
            send_step_prompt(client, channel, thread_ts, session)

    elif step == "confirm":
        # Build the quote
        quote_units = [{"pricing": u["pricing"], "qty": u["qty"]} for u in session["units"]]
        quote = calculate_quote(quote_units, session["discount"])
        session["last_quote"] = quote
        set_session(thread_ts, session)

        blocks = build_quote_summary_blocks(quote, session)
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                "Type `confirm` to save this quote, `edit` to go back and change units, "
                "or `restart` to start over."
            )},
        })

        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            blocks=blocks,
            text=f"Quote for {session['partner']}: {format_currency(quote['final_total'])} per cycle",
        )


def handle_reply(client, channel, thread_ts, user_id, text, session):
    """Process user reply for the current step and advance."""
    step = session["step"]
    text = text.strip()

    # Global commands
    if text.lower() in ("cancel", "stop"):
        clear_session(thread_ts)
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=":x: Quote cancelled. Use `/quote` to start a new one.",
        )
        return

    if text.lower() == "restart":
        start_session(thread_ts, channel, user_id)
        session = get_session(thread_ts)
        send_step_prompt(client, channel, thread_ts, session)
        return

    # ── Step handlers ───────────────────────────────────

    if step == "ask_partner":
        session["partner"] = text
        session["step"] = "ask_zip"
        set_session(thread_ts, session)
        send_step_prompt(client, channel, thread_ts, session)

    elif step == "ask_zip":
        zip_code = re.sub(r"\D", "", text)[:5]
        if len(zip_code) < 5:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=":warning: Please enter a valid 5-digit zip code.",
            )
            return

        tier = get_market_tier(zip_code)
        if not tier:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=":warning: Couldn't determine market tier. Please try another zip code.",
            )
            return

        session["zip"] = zip_code
        session["tier"] = tier
        session["step"] = "ask_unit_config"
        set_session(thread_ts, session)

        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":round_pushpin: *{zip_code}* → *{TIER_LABELS[tier]}* (Tier {tier}). Rates adjusted to this market.",
        )
        send_step_prompt(client, channel, thread_ts, session)

    elif step == "ask_unit_config":
        # Parse beds/baths from input like "2/1", "2-1", "2 1", "studio"
        t = text.lower().strip()
        if "studio" in t:
            beds, baths = "0", "1"
        else:
            match = re.match(r"(\d)\s*[/\-\s]\s*(\d)", t)
            if not match:
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text=":warning: Please enter the format as `beds/baths` (e.g., `2/1`). Type `studio` for a studio.",
                )
                return
            beds, baths = match.group(1), match.group(2)

        key = f"{beds}-{baths}"
        if key not in VALID_CONFIGS:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f":warning: `{beds}BR/{baths}BA` isn't a supported config. Available: {', '.join(UNIT_LABELS[k] for k in VALID_CONFIGS)}",
            )
            return

        session["current_unit"] = {"beds": beds, "baths": baths}
        session["step"] = "ask_unit_qty"
        set_session(thread_ts, session)
        send_step_prompt(client, channel, thread_ts, session)

    elif step == "ask_unit_qty":
        try:
            qty = int(text)
            if qty < 1:
                raise ValueError
        except ValueError:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=":warning: Please enter a number (1 or more).",
            )
            return

        session["current_unit"]["qty"] = qty
        session["step"] = "ask_addons"
        set_session(thread_ts, session)
        send_step_prompt(client, channel, thread_ts, session)

    elif step == "ask_addons":
        t = text.lower().strip()
        deep = t in ("deep", "both", "deep clean", "all")
        laundry = t in ("laundry", "both", "all")
        # Also accept comma-separated
        if "," in t:
            parts = [p.strip() for p in t.split(",")]
            deep = any("deep" in p for p in parts)
            laundry = any("laundry" in p or "lau" in p for p in parts)

        cu = session["current_unit"]
        pricing = calculate_unit_price(
            tier=session["tier"],
            beds=cu["beds"],
            baths=cu["baths"],
            deep_clean=deep,
            laundry=laundry,
        )

        session["units"].append({
            "beds": cu["beds"],
            "baths": cu["baths"],
            "qty": cu["qty"],
            "deep_clean": deep,
            "laundry": laundry,
            "pricing": pricing,
        })
        session["current_unit"] = {}
        session["step"] = "ask_more_units"
        set_session(thread_ts, session)
        send_step_prompt(client, channel, thread_ts, session)

    elif step == "ask_more_units":
        t = text.lower().strip()
        if t in ("add", "another", "more", "yes", "+"):
            session["step"] = "ask_unit_config"
            set_session(thread_ts, session)
            send_step_prompt(client, channel, thread_ts, session)
        elif t in ("done", "finish", "finalize", "no", "next"):
            session["step"] = "ask_discount"
            set_session(thread_ts, session)
            send_step_prompt(client, channel, thread_ts, session)
        else:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text="Type `add` to add another unit type, or `done` to finalize.",
            )

    elif step == "ask_discount":
        try:
            disc = float(text.replace("%", "").strip())
            disc = max(0, min(15, disc))
        except ValueError:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=":warning: Please enter a number between 0 and 15.",
            )
            return

        session["discount"] = disc

        # Margin warning check
        quote_units = [{"pricing": u["pricing"], "qty": u["qty"]} for u in session["units"]]
        test_quote = calculate_quote(quote_units, disc)
        if not test_quote["margin_ok"] and disc > 0:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=(
                    f":warning: At {disc}% discount, the lowest margin drops to *{test_quote['worst_margin']}%* "
                    f"(below the 25% target). Enter a lower discount or type `ok` to proceed anyway."
                ),
            )
            session["step"] = "discount_confirm"
            set_session(thread_ts, session)
            return

        session["step"] = "confirm"
        set_session(thread_ts, session)
        send_step_prompt(client, channel, thread_ts, session)

    elif step == "discount_confirm":
        t = text.lower().strip()
        if t in ("ok", "yes", "proceed", "continue"):
            session["step"] = "confirm"
            set_session(thread_ts, session)
            send_step_prompt(client, channel, thread_ts, session)
        else:
            try:
                disc = float(t.replace("%", ""))
                disc = max(0, min(15, disc))
                session["discount"] = disc
                session["step"] = "confirm"
                set_session(thread_ts, session)
                send_step_prompt(client, channel, thread_ts, session)
            except ValueError:
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text="Enter a new discount percentage or type `ok` to proceed with the current one.",
                )

    elif step == "confirm":
        t = text.lower().strip()
        if t in ("confirm", "save", "yes", "looks good", "lgtm"):
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=(
                    f":white_check_mark: *Quote saved!* — {session['partner']} | "
                    f"{format_currency(session['last_quote']['final_total'])} per cycle | "
                    f"{session['last_quote']['total_units']} units\n\n"
                    f"_Use `/quote` to start a new one._"
                ),
            )
            clear_session(thread_ts)
        elif t in ("edit", "back", "change"):
            session["step"] = "ask_unit_config"
            session["units"] = []
            set_session(thread_ts, session)
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=":pencil2: Let's redo the units. Starting over on unit configuration...",
            )
            send_step_prompt(client, channel, thread_ts, session)
        elif t == "restart":
            handle_reply(client, channel, thread_ts, user_id, "restart", session)
        else:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text="Type `confirm` to save, `edit` to redo units, or `restart` to start fresh.",
            )


# ── Slash Command ───────────────────────────────────────

@app.command("/quote")
def handle_quote_command(ack, command, client):
    ack()
    channel = command["channel_id"]
    user_id = command["user_id"]

    # Post initial message to create a thread
    result = client.chat_postMessage(
        channel=channel,
        text=(
            f":clipboard: <@{user_id}> started a new cleaning quote.\n"
            f"_Reply in this thread to build the quote step by step. "
            f"Type `cancel` at any point to stop._"
        ),
    )

    thread_ts = result["ts"]
    start_session(thread_ts, channel, user_id)
    session = get_session(thread_ts)
    send_step_prompt(client, channel, thread_ts, session)


# ── Thread Reply Handler ────────────────────────────────

@app.event("message")
def handle_message(event, client):
    # Only handle threaded replies
    thread_ts = event.get("thread_ts")
    if not thread_ts:
        return

    # Ignore bot messages
    if event.get("bot_id") or event.get("subtype"):
        return

    session = get_session(thread_ts)
    if not session:
        return

    channel = event["channel"]
    user_id = event.get("user", "")
    text = event.get("text", "")

    handle_reply(client, channel, thread_ts, user_id, text, session)


# ── App Home (optional welcome) ─────────────────────────

@app.event("app_home_opened")
def handle_app_home(event, client):
    client.views_publish(
        user_id=event["user"],
        view={
            "type": "home",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "Turnover Clean Quote Bot"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": (
                    "Use `/quote` in any channel to start building a cleaning quote.\n\n"
                    "The bot will walk you through:\n"
                    "1. Partner name\n"
                    "2. Property zip code (auto-detects market tier)\n"
                    "3. Unit types and quantities\n"
                    "4. Add-on services\n"
                    "5. Volume discounts\n\n"
                    "All pricing includes labor, supplies, and supervisor oversight."
                )}},
            ],
        },
    )


# ── Start ───────────────────────────────────────────────

if __name__ == "__main__":
    # Socket Mode for development (no public URL needed)
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    logger.info("Quote bot starting in Socket Mode...")
    handler.start()
