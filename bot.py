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

Optional repo secrets TELEGRAM_NOTIFY_TOKEN/TELEGRAM_NOTIFY_CHAT_ID (the
Midas human-notification bot — sendMessage only, no getUpdates polling, so
reusing that token here does not steal its updates) push a message to the
human on every real sale/tip. Without this, a sale is only visible to the
buyer themselves — nobody running this project would know it happened.
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import urllib.parse

from custom_invoice import parse_invoice_text, render_invoice, preview_text, USAGE

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
BASE_URL = "https://kleogeos.github.io/freelancer-tools"
STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")
FULFILLED_PATH = os.path.join(os.path.dirname(__file__), "fulfilled.json")
PENDING_PATH = os.path.join(os.path.dirname(__file__), "pending_orders.json")

# Custom-invoice-via-chat flow: same bot/token, new payload prefix
# "custom-invoice:<order_id>". Priced like the cheapest existing product
# (ru-kit is 530 stars) but lower, since there's no bundle of documents —
# just one generated file, closer to an impulse buy than the Starter Kit.
CUSTOM_INVOICE_STARS = 100
# Abandoned previews (parsed but never paid) are pruned after this long —
# no reason to keep a stranger's invoice data around indefinitely.
PENDING_TTL_SEC = 48 * 60 * 60

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


def send_document(chat_id, filename, content_bytes, caption=None):
    # sendDocument needs multipart/form-data, unlike every other call in this
    # file (plain JSON) — built by hand rather than pulling in `requests`, to
    # keep this bot's only dependency the Python standard library (same
    # zero-deps rule as the rest of this project's tools). Mirrors api_call's
    # retry loop rather than reusing it, since api_call is JSON-only and used
    # by every other call site — not worth complicating its signature for
    # this one multipart caller.
    boundary = secrets.token_hex(16)
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode())
    if caption:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode()
        )
    parts.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
         f"Content-Type: text/html\r\n\r\n").encode()
        + content_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{API}/sendDocument", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
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


def notify_human(text):
    token = os.environ.get("TELEGRAM_NOTIFY_TOKEN")
    chat_id = os.environ.get("TELEGRAM_NOTIFY_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
    except Exception as e:  # noqa: BLE001 — a notify failure must never break fulfillment
        print(f"[warn] telegram notify failed: {e}")


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def prune_pending(pending):
    now = time.time()
    expired = [oid for oid, o in pending.items() if now - o.get("ts", 0) > PENDING_TTL_SEC]
    for oid in expired:
        del pending[oid]
    return pending


def handle_message(message, pending):
    text = message.get("text")
    if not text:
        return  # photos/stickers/etc — nothing for this bot to act on
    chat_id = message["chat"]["id"]
    stripped = text.strip()
    if stripped.lower() in ("/start", "/invoice"):
        api_call("sendMessage", chat_id=chat_id, text=USAGE)
        return
    if not stripped.lower().startswith("/invoice"):
        return  # don't reply to every message in a group this bot might be added to

    data, err = parse_invoice_text(text)
    if err:
        api_call("sendMessage", chat_id=chat_id, text=err)
        return

    order_id = secrets.token_hex(4)
    pending[order_id] = {"chat_id": chat_id, "data": data, "ts": time.time()}
    resp = api_call(
        "createInvoiceLink",
        title="Custom Invoice",
        description=f"Generated from your message — {data['from']} to {data['to']}",
        payload=f"custom-invoice:{order_id}",
        currency="XTR",
        prices=[{"label": "Custom Invoice", "amount": CUSTOM_INVOICE_STARS}],
    )
    if not resp.get("ok"):
        del pending[order_id]
        api_call("sendMessage", chat_id=chat_id,
                  text="Something went wrong generating your payment link — please try again.")
        print(f"[WARN] createInvoiceLink failed: {resp}")
        return
    api_call(
        "sendMessage",
        chat_id=chat_id,
        text=f"{preview_text(data)}\n\nPay to receive the polished HTML file:\n{resp['result']}",
    )
    print(f"[pending] order {order_id} -> chat {chat_id}")


def handle_pre_checkout(pcq, pending):
    # Must answer within 10s. payload must be a known product, otherwise reject
    # so no one can be charged for a typo'd/unknown payload.
    payload = pcq.get("invoice_payload", "")
    ok = (
        payload in PRODUCTS
        or payload in TIPS
        or (payload.startswith("custom-invoice:") and payload.split(":", 1)[1] in pending)
    )
    kwargs = {"pre_checkout_query_id": pcq["id"], "ok": ok}
    if not ok:
        kwargs["error_message"] = "Unknown product, please use the link from the website."
    api_call("answerPreCheckoutQuery", **kwargs)
    print(f"[pre_checkout] payload={payload!r} ok={ok}")


def handle_successful_payment(message, fulfilled, pending=None):
    sp = message["successful_payment"]
    charge_id = sp["telegram_payment_charge_id"]
    payload = sp.get("invoice_payload", "")
    chat_id = message["chat"]["id"]
    if charge_id in fulfilled:
        return  # already delivered, never double-send
    if payload.startswith("custom-invoice:"):
        order_id = payload.split(":", 1)[1]
        order = (pending or {}).pop(order_id, None)
        if order is None:
            # Buyer was already charged by Telegram at this point. This can
            # happen if pending_orders.json and the actual payment landed in
            # different GitHub Actions job runs and the state file didn't
            # carry the order over (same race class as fulfilled.json —
            # documented there). Must notify, not drop silently.
            print(f"[WARN] successful_payment for unknown custom-invoice order_id={order_id!r} charge_id={charge_id}")
            notify_human(
                f"URGENT: charged {sp['total_amount']} Stars for custom invoice "
                f"order {order_id!r} (charge {charge_id[:12]}...) — buyer paid, "
                f"no pending order data found, needs manual refund or fulfillment."
            )
            return
        html_out = render_invoice(order["data"])
        send_document(chat_id, "invoice.html", html_out.encode("utf-8"),
                      caption="Thanks for your order! Here's your invoice.")
        fulfilled[charge_id] = {"product": "Custom Invoice", "stars": sp["total_amount"]}
        save_json(FULFILLED_PATH, fulfilled)
        print(f"[fulfilled] custom-invoice {order_id} -> chat {chat_id} (charge {charge_id[:12]}...)")
        notify_human(f"Sale! Custom Invoice ({sp['total_amount']} Stars, charge {charge_id[:12]}...)")
        return
    if payload in TIPS:
        api_call(
            "sendMessage",
            chat_id=chat_id,
            text="Thank you for the tip! It genuinely helps keep the free tools maintained.",
        )
        fulfilled[charge_id] = {"product": payload, "stars": sp["total_amount"]}
        save_json(FULFILLED_PATH, fulfilled)
        print(f"[fulfilled] tip -> chat {chat_id} (charge {charge_id[:12]}...)")
        notify_human(f"Tip! {sp['total_amount']} Stars (charge {charge_id[:12]}...)")
        return
    paths, name = PRODUCTS.get(payload, (None, None))
    if not paths:
        # The buyer has already been charged Stars by Telegram at this point —
        # pre_checkout only blocks the charge for unknown payloads, it can't
        # stop PRODUCTS changing between invoice-link creation and payment
        # (e.g. a product removed/renamed after an old link was already
        # shared). Silently returning here used to mean: buyer paid, nobody
        # delivered anything, and nobody was ever told — the one outcome
        # worth paging a human for immediately, so notify_human instead of
        # just logging to a workflow log nobody reads in time.
        print(f"[WARN] successful_payment with unknown payload={payload!r} charge_id={charge_id}")
        notify_human(
            f"URGENT: charged {sp['total_amount']} Stars for unknown payload "
            f"{payload!r} (charge {charge_id[:12]}...) — buyer paid, nothing "
            f"delivered, needs manual refund or fulfillment."
        )
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
    notify_human(f"Sale! {name} ({sp['total_amount']} Stars, charge {charge_id[:12]}...)")


def main():
    if not TOKEN:
        sys.exit(
            "TELEGRAM_BOT_TOKEN is not set — this is expected until activation. "
            "See README.md 'To activate'. Not treating this as a crash."
        )
    state = load_json(STATE_PATH, {"offset": 0})
    fulfilled = load_json(FULFILLED_PATH, {})
    pending = prune_pending(load_json(PENDING_PATH, {}))
    deadline = time.time() + MAX_RUNTIME_SEC

    while time.time() < deadline:
        try:
            resp = api_call("getUpdates", offset=state["offset"], timeout=30,
                             allowed_updates=["pre_checkout_query", "message"])
            for update in resp.get("result", []):
                state["offset"] = update["update_id"] + 1
                if "pre_checkout_query" in update:
                    handle_pre_checkout(update["pre_checkout_query"], pending)
                elif "message" in update and "successful_payment" in update["message"]:
                    handle_successful_payment(update["message"], fulfilled, pending)
                    save_json(PENDING_PATH, pending)
                elif "message" in update:
                    handle_message(update["message"], pending)
                    save_json(PENDING_PATH, pending)
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

