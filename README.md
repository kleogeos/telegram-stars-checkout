# telegram-stars-checkout

Fulfillment responder for selling digital goods via [Telegram Stars](https://core.telegram.org/bots/payments-stars) (currency `XTR`) — no bank, no card network, no Stripe/PayPal, no KYC on the selling side.

**Status: code complete and tested, not yet activated.** Needs a dedicated bot token (see below) — do not reuse a bot that's already polled by something else.

## Why this exists

Telegram requires `answerPreCheckoutQuery` within 10 seconds of every payment attempt, for any currency including Stars — there's no auto-approve. This repo runs that responder inside a scheduled GitHub Actions job: public-repo Actions minutes are unlimited, so a ~5h50m long-poll per run, re-triggered by cron every 6 hours, gives near-continuous coverage for $0 and no extra hosting account.

## Files

- `bot.py` — long-polling responder: answers `pre_checkout_query`, and on `successful_payment` sends the buyer their download link. Payloads not in `PRODUCTS` are rejected before any charge happens. Charge IDs are logged so a replayed update never double-delivers.
- `test_bot.py` — offline unit tests (mocked Telegram API, no network/token needed): `python3 -m unittest test_bot.py`.
- `.github/workflows/telegram-stars-bot.yml` — the scheduled runner.
- `state.json` / `fulfilled.json` — persisted between runs so restarts don't reprocess old updates or double-deliver.

## To activate

1. Create a **new, dedicated** bot via @BotFather (not the Midas notification bot).
2. Add its token as the `TELEGRAM_BOT_TOKEN` repo secret.
3. Generate invoice links per product with `createInvoiceLink` (currency `XTR`, payload matching a key in `bot.py`'s `PRODUCTS` dict) and add them as "Pay with Telegram Stars" buttons on the existing checkout pages.
4. Trigger the workflow once manually (`workflow_dispatch`) to confirm it runs, then let the schedule take over.

Full research and pricing draft: see `TELEGRAM-STARS-RESEARCH.md` in the main workspace.
