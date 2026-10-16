#!/usr/bin/env python3
"""Generate stable Telegram Stars (XTR) invoice links for each product.

createInvoiceLink is safe to call against ANY bot token — unlike getUpdates,
it does not consume/steal updates from another consumer of the same bot, so
this can be (and was) tested live against the existing Midas notification
bot without affecting it. For real activation, run this with the DEDICATED
bot's token (see README.md) — the links it returns are tied to whichever
bot generated them and must match the bot that bot.py polls for payments.

Star prices are based on the ~$0.013/star rate Telegram actually credits
sellers for Stars revenue (regardless of how the buyer paid — Fragment,
Apple/Google IAP markup is absorbed by Telegram, not passed to the seller).
Verify against the live rate before publishing — see TELEGRAM-STARS-RESEARCH.md.

Usage: TELEGRAM_BOT_TOKEN=... python3 create_invoice_links.py
"""
import json
import os
import urllib.request

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

# payload -> (title, description, stars) — payload keys must match bot.py's PRODUCTS
PRODUCTS = {
    "starter-kit": (
        "Freelancer Starter Kit",
        "Proposal, contract, invoice + late-payment scripts, rate calculator, "
        "client intake questionnaire. 7 documents, plain Markdown.",
        1150,
    ),
    "prompt-pack": (
        "40 AI Prompts for Freelancers",
        "40 tested prompts for proposals, outreach, scope negotiation, and client replies.",
        690,
    ),
    "dev-pack": (
        "Web Developer Pack",
        "Developer-specific contract clauses, scope templates, and client onboarding docs.",
        920,
    ),
    "bundle": (
        "Bundle (Starter Kit + Prompt Pack + Dev Pack)",
        "All three kits together.",
        1920,
    ),
    "ru-kit": (
        "RU Crypto Freelancer Kit",
        "Invoice template, payment reminders, self-employment/115-FZ checklist, "
        "crypto income log — for RU/CIS freelancers billing foreign clients.",
        530,
    ),
}

# payload -> stars, must match bot.py's TIPS set
TIPS = {
    "tip-100": 100,
    "tip-300": 300,
    "tip-1000": 1000,
}


def create_invoice_link(payload, title, description, stars):
    data = json.dumps({
        "title": title,
        "description": description,
        "payload": payload,
        "currency": "XTR",
        "prices": [{"label": title, "amount": stars}],
    }).encode()
    req = urllib.request.Request(
        f"{API}/createInvoiceLink", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main():
    results = {}
    for payload, (title, description, stars) in PRODUCTS.items():
        resp = create_invoice_link(payload, title, description, stars)
        if resp.get("ok"):
            results[payload] = {"link": resp["result"], "stars": stars, "title": title}
            print(f"[ok] {payload} ({stars} stars) -> {resp['result']}")
        else:
            print(f"[FAIL] {payload}: {resp}")
    for payload, stars in TIPS.items():
        resp = create_invoice_link(
            payload, f"Tip: {stars} Stars",
            "Support the free Freelancer Toolbox tools — no download attached.",
            stars,
        )
        if resp.get("ok"):
            results[payload] = {"link": resp["result"], "stars": stars, "title": f"Tip: {stars} Stars"}
            print(f"[ok] {payload} ({stars} stars) -> {resp['result']}")
        else:
            print(f"[FAIL] {payload}: {resp}")
    out_path = os.path.join(os.path.dirname(__file__), "invoice_links.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
