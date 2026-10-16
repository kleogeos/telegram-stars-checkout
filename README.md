# telegram-stars-checkout

![Test](https://github.com/kleogeos/telegram-stars-checkout/actions/workflows/test.yml/badge.svg)

Fulfillment responder for selling digital goods via [Telegram Stars](https://core.telegram.org/bots/payments-stars) (currency `XTR`) — no bank, no card network, no Stripe/PayPal, no KYC on the selling side.

**Status: ACTIVE.** Dedicated bot [@freelancerkitbot](https://t.me/freelancerkitbot) is live, `TELEGRAM_BOT_TOKEN` is set, the scheduled responder runs on cron, and "Pay with Telegram Stars" buttons are published on all 5 checkout pages (starter-kit, prompt-pack, dev-pack, bundle, ru-kit).

## Why this exists

Telegram requires `answerPreCheckoutQuery` within 10 seconds of every payment attempt, for any currency including Stars — there's no auto-approve. This repo runs that responder inside a scheduled GitHub Actions job: public-repo Actions minutes are unlimited, so a ~5h50m long-poll per run, re-triggered by cron every 6 hours, gives near-continuous coverage for $0 and no extra hosting account.

## Files

- `bot.py` — long-polling responder: answers `pre_checkout_query`, and on `successful_payment` sends the buyer their download link. Payloads not in `PRODUCTS` are rejected before any charge happens. Charge IDs are logged so a replayed update never double-delivers.
- `test_bot.py` — offline unit tests (mocked Telegram API, no network/token needed): `python3 -m unittest test_bot.py`.
- `.github/workflows/telegram-stars-bot.yml` — the scheduled runner (schedule trigger disabled until activation, see comment in the file).
- `.github/workflows/test.yml` — runs `test_bot.py` on every push.
- `create_invoice_links.py` — generates the 5 checkout links (starter-kit, prompt-pack, dev-pack, bundle, ru-kit). Star prices use the ~$0.013/star rate Telegram actually credits sellers.
- `state.json` / `fulfilled.json` — persisted between runs so restarts don't reprocess old updates or double-deliver.

## Adding a new product

1. Add the payload to `PRODUCTS` in `bot.py` (download path(s) + display name) and to `PRODUCTS` in `create_invoice_links.py` (title/description/star price).
2. Push, then cancel+redispatch the in-flight workflow run so the next long-poll checks out the new code (it only reads `PRODUCTS` once at start).
3. Run `TELEGRAM_BOT_TOKEN=... python3 create_invoice_links.py` to mint a fresh link for every product (old links stay valid, but regenerating keeps `invoice_links.json` as the single source of truth) and update the checkout page(s).

Full research and pricing draft: see `TELEGRAM-STARS-RESEARCH.md` in the main workspace.
