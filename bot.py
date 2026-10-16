#!/usr/bin/env python3
"""Long-polling responder for Telegram Stars (XTR) digital-goods checkout.

Why this exists: Telegram requires answerPreCheckoutQuery within 10 seconds
of every payment attempt, for any currency including XTR (Stars). There is
no "auto-approve" default. Without a process answering that query, the buyer
sees BOT_PRECHECKOUT_TIMEOUT and the payment fails.

Why GitHub Actions: this script is designed to run inside a scheduled
GitHub Actions job (see ../.github/workflows/telegram-stars-bot.yml in the
dedicated repo this gets pushed to). Actions minutes are unlimited for
public repos, so a job that long-polls for ~5h50m and gets re-triggered by
cron before it's killed gives near-continuous coverage for $0 and no new
hosting account. Not literally 24/7 (a few seconds of restart gap between
runs), but far better than nothing, and good enough for a low-volume
digital-goods checkout.

Requires a DEDICATED bot token (not the Midas human-notification bot) —
polling getUpdates on a token already being read by another consumer would
steal that consumer's updates. Set TELEGRAM_BOT_TOKEN as a repo secret.

State: last processed update_id is persisted to state.json and committed
back so a restart doesn't reprocess old updates. Fulfilled payments are
logged to fulfilled.json (charge_id -> product) so a replayed update never
double-delivers.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import urllib.parse

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
BASE_URL = "https://kleogeos.github.io/freelancer-tools"
STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")
FULFILLED_PATH = os.path.join(os.path.dirname(__file__), "fulfilled.json")

# invoice_payload -> (download path(s), display name)
PRODUCTS = {
    "starter-kit": (["files/freelancer-kit.zip"], "Freelancer Starter Kit"),
    "prompt-pack": (["files/prompt-pack.zip"], "40 AI Prompts for Freelancers"),
    "dev-pack": (["files/dev-pack.zip"], "Web Developer Pack"),
    "bundle": (
        ["files/freelancer-kit.zip", "files/prompt-pack.zip", "files/dev-pack.zip"],
        "Bundle (all three)",
    ),
    "ru-kit": (["files/ru-crypto-kit.zip"], "RU Crypto Freelancer Kit"),
}

# Tip-jar payloads (donate.html) — no file to deliver, just a thank-you.
TIPS = {"tip-100", "tip-300", "tip-1000"}

MAX_RUNTIME_SEC = 5 * 60 * 60 + 50 * 60  # 5h50m, stay under GitHub Actions' 6h job cap


def api_call(method, **params):
    # GitHub-hosted runners occasionally hit a transient "Network is
    # unreachable" blip (seen 3 times in one day: 2026-10-16). Without a
    # retry, that single blip used to kill the whole long-poll process,
    # leaving the bot unable to answer checkouts until the next 6h cron
    # slot — a real lost sale, not just a delay, since pre-checkout queries
    # must be answered within 10s. A few quick retries absorb the blip
    # without materially eating into that 10s budget.
    data = json.dumps(params).encode()
    req = urllib.request.Request(
        f"{API}/{method}", data=data, headers={"Content-Type": "application/json"}
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < 2:
                time.sleep(1 + attempt)
    raise last_err


def load_json(path, default):
    if os.path.exists(path):
        return json.load(open(path))
    return default


def save_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def handle_pre_checkout(pcq):
    # Must answer within 10s. payload must be a known product, otherwise reject
    # so no one can be charged for a typo'd/unknown payload.
    payload = pcq.get("invoice_payload", "")
    ok = payload in PRODUCTS or payload in TIPS
    kwargs = {"pre_checkout_query_id": pcq["id"], "ok": ok}
    if not ok:
        kwargs["error_message"] = "Unknown product, please use the link from the website."
    api_call("answerPreCheckoutQuery", **kwargs)
    print(f"[pre_checkout] payload={payload!r} ok={ok}")


def handle_successful_payment(message, fulfilled):
    sp = message["successful_payment"]
    charge_id = sp["telegram_payment_charge_id"]
    payload = sp.get("invoice_payload", "")
    chat_id = message["chat"]["id"]
    if charge_id in fulfilled:
        return  # already delivered, never double-send
    if payload in TIPS:
        api_call(
            "sendMessage",
            chat_id=chat_id,
            text="Thank you for the tip! It genuinely helps keep the free tools maintained.",
        )
        fulfilled[charge_id] = {"product": payload, "stars": sp["total_amount"]}
        save_json(FULFILLED_PATH, fulfilled)
        print(f"[fulfilled] tip -> chat {chat_id} (charge {charge_id[:12]}...)")
        return
    paths, name = PRODUCTS.get(payload, (None, None))
    if not paths:
        print(f"[WARN] successful_payment with unknown payload={payload!r} charge_id={charge_id}")
        return
    links = "\n".join(f"{BASE_URL}/{p}" for p in paths)
    api_call(
        "sendMessage",
        chat_id=chat_id,
        text=f"Thanks for your order! Here's your download:\n\n{links}\n\nEnjoy!",
    )
    fulfilled[charge_id] = {"product": name, "stars": sp["total_amount"]}
    save_json(FULFILLED_PATH, fulfilled)
    print(f"[fulfilled] {name} -> chat {chat_id} (charge {charge_id[:12]}...)")


def main():
    if not TOKEN:
        sys.exit(
            "TELEGRAM_BOT_TOKEN is not set — this is expected until activation. "
            "See README.md 'To activate'. Not treating this as a crash."
        )
    state = load_json(STATE_PATH, {"offset": 0})
    fulfilled = load_json(FULFILLED_PATH, {})
    deadline = time.time() + MAX_RUNTIME_SEC

    while time.time() < deadline:
        try:
            resp = api_call("getUpdates", offset=state["offset"], timeout=30,
                             allowed_updates=["pre_checkout_query", "message"])
            for update in resp.get("result", []):
                state["offset"] = update["update_id"] + 1
                if "pre_checkout_query" in update:
                    handle_pre_checkout(update["pre_checkout_query"])
                elif "message" in update and "successful_payment" in update["message"]:
                    handle_successful_payment(update["message"], fulfilled)
            save_json(STATE_PATH, state)
        except (urllib.error.URLError, TimeoutError) as e:
            # Retries inside api_call already absorbed short blips; if we still
            # land here the network is down for longer. Sleep and keep polling
            # within this same job instead of exiting and waiting hours for
            # the next cron slot.
            print(f"[WARN] getUpdates failed, backing off 30s: {e}")
            time.sleep(30)

    print("Runtime budget reached, exiting cleanly for the next scheduled run.")


if __name__ == "__main__":
    main()
