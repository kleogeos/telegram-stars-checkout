#!/usr/bin/env python3
"""Offline tests for bot.py's decision logic. Mocks api_call so no network/token
is needed and the real bot (used for human notifications) is never touched."""
import http.client
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestBotLogic(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        import bot
        self.bot = bot
        self.bot.STATE_PATH = os.path.join(self.tmpdir, "state.json")
        self.bot.FULFILLED_PATH = os.path.join(self.tmpdir, "fulfilled.json")
        self.calls = []

        def fake_api_call(method, **params):
            self.calls.append((method, params))
            return {"ok": True, "result": True}

        self.patcher = mock.patch.object(self.bot, "api_call", fake_api_call)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_known_payload_is_accepted(self):
        self.bot.handle_pre_checkout({"id": "pcq1", "invoice_payload": "starter-kit"}, {})
        method, params = self.calls[-1]
        self.assertEqual(method, "answerPreCheckoutQuery")
        self.assertEqual(params["pre_checkout_query_id"], "pcq1")
        self.assertTrue(params["ok"])
        self.assertNotIn("error_message", params)

    def test_unknown_payload_is_rejected(self):
        self.bot.handle_pre_checkout({"id": "pcq2", "invoice_payload": "typo-product"}, {})
        method, params = self.calls[-1]
        self.assertFalse(params["ok"])
        self.assertIn("error_message", params)

    def test_successful_payment_sends_correct_links(self):
        fulfilled = {}
        message = {
            "chat": {"id": 42},
            "successful_payment": {
                "telegram_payment_charge_id": "ch1",
                "invoice_payload": "bundle",
                "total_amount": 1390,
            },
        }
        self.bot.handle_successful_payment(message, fulfilled)
        method, params = self.calls[-1]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(params["chat_id"], 42)
        self.assertIn("freelancer-kit.zip", params["text"])
        self.assertIn("prompt-pack.zip", params["text"])
        self.assertIn("dev-pack.zip", params["text"])
        self.assertIn("ch1", fulfilled)

    def test_successful_payment_notifies_human(self):
        notified = []
        fulfilled = {}
        message = {
            "chat": {"id": 42},
            "successful_payment": {
                "telegram_payment_charge_id": "ch1",
                "invoice_payload": "bundle",
                "total_amount": 1390,
            },
        }
        with mock.patch.object(self.bot, "notify_human", side_effect=lambda t: notified.append(t)):
            self.bot.handle_successful_payment(message, fulfilled)
        self.assertEqual(len(notified), 1)
        self.assertIn("Sale!", notified[0])
        self.assertIn("1390", notified[0])

    def test_tip_notifies_human(self):
        notified = []
        fulfilled = {}
        message = {
            "chat": {"id": 9},
            "successful_payment": {
                "telegram_payment_charge_id": "ch4",
                "invoice_payload": "tip-300",
                "total_amount": 300,
            },
        }
        with mock.patch.object(self.bot, "notify_human", side_effect=lambda t: notified.append(t)):
            self.bot.handle_successful_payment(message, fulfilled)
        self.assertEqual(len(notified), 1)
        self.assertIn("Tip!", notified[0])

    def test_duplicate_charge_id_does_not_renotify(self):
        notified = []
        fulfilled = {"ch1": {"product": "Bundle (all three)", "stars": 1390}}
        message = {
            "chat": {"id": 42},
            "successful_payment": {
                "telegram_payment_charge_id": "ch1",
                "invoice_payload": "bundle",
                "total_amount": 1390,
            },
        }
        with mock.patch.object(self.bot, "notify_human", side_effect=lambda t: notified.append(t)):
            self.bot.handle_successful_payment(message, fulfilled)
        self.assertEqual(notified, [])

    def test_notify_human_is_a_noop_without_secrets(self):
        os.environ.pop("TELEGRAM_NOTIFY_TOKEN", None)
        os.environ.pop("TELEGRAM_NOTIFY_CHAT_ID", None)
        with mock.patch("urllib.request.urlopen") as m:
            self.bot.notify_human("should not send")
        m.assert_not_called()

    def test_duplicate_charge_id_is_not_delivered_twice(self):
        fulfilled = {"ch1": {"product": "Bundle (all three)", "stars": 1390}}
        message = {
            "chat": {"id": 42},
            "successful_payment": {
                "telegram_payment_charge_id": "ch1",
                "invoice_payload": "bundle",
                "total_amount": 1390,
            },
        }
        n_calls_before = len(self.calls)
        self.bot.handle_successful_payment(message, fulfilled)
        self.assertEqual(len(self.calls), n_calls_before)  # no sendMessage call made

    def test_single_product_payload_sends_one_link(self):
        fulfilled = {}
        message = {
            "chat": {"id": 7},
            "successful_payment": {
                "telegram_payment_charge_id": "ch2",
                "invoice_payload": "prompt-pack",
                "total_amount": 500,
            },
        }
        self.bot.handle_successful_payment(message, fulfilled)
        method, params = self.calls[-1]
        self.assertIn("prompt-pack.zip", params["text"])
        self.assertNotIn("freelancer-kit.zip", params["text"])

    def test_tip_payload_sends_thank_you_without_links(self):
        fulfilled = {}
        message = {
            "chat": {"id": 9},
            "successful_payment": {
                "telegram_payment_charge_id": "ch4",
                "invoice_payload": "tip-300",
                "total_amount": 300,
            },
        }
        self.bot.handle_successful_payment(message, fulfilled)
        method, params = self.calls[-1]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(params["chat_id"], 9)
        self.assertNotIn(".zip", params["text"])
        self.assertIn("ch4", fulfilled)

    def test_tip_payload_is_accepted_at_pre_checkout(self):
        self.bot.handle_pre_checkout({"id": "pcq3", "invoice_payload": "tip-1000"}, {})
        method, params = self.calls[-1]
        self.assertTrue(params["ok"])
        self.assertNotIn("error_message", params)

    def test_unknown_payload_on_successful_payment_does_not_crash_or_send(self):
        fulfilled = {}
        message = {
            "chat": {"id": 7},
            "successful_payment": {
                "telegram_payment_charge_id": "ch3",
                "invoice_payload": "nonexistent",
                "total_amount": 100,
            },
        }
        n_calls_before = len(self.calls)
        self.bot.handle_successful_payment(message, fulfilled)
        self.assertEqual(len(self.calls), n_calls_before)
        self.assertNotIn("ch3", fulfilled)

    def test_unknown_payload_on_successful_payment_pages_human(self):
        # The buyer was already charged by Telegram by this point (payload
        # mismatches only happen if PRODUCTS changed after an invoice link was
        # minted/shared) — silently dropping this would mean a paid buyer gets
        # nothing and nobody ever finds out. Must notify, unlike a normal
        # pre_checkout rejection which happens before any money moves.
        notified = []
        fulfilled = {}
        message = {
            "chat": {"id": 7},
            "successful_payment": {
                "telegram_payment_charge_id": "ch3",
                "invoice_payload": "nonexistent",
                "total_amount": 100,
            },
        }
        with mock.patch.object(self.bot, "notify_human", side_effect=lambda t: notified.append(t)):
            self.bot.handle_successful_payment(message, fulfilled)
        self.assertEqual(len(notified), 1)
        self.assertIn("URGENT", notified[0])
        self.assertIn("100", notified[0])


class TestApiCallRetry(unittest.TestCase):
    def setUp(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        import bot
        self.bot = bot

    def test_transient_network_error_is_retried_and_succeeds(self):
        calls = {"n": 0}

        def flaky_urlopen(req, timeout=40):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("Network is unreachable")
            return mock.mock_open(read_data=b'{"ok": true}')()

        with mock.patch("urllib.request.urlopen", side_effect=flaky_urlopen), \
             mock.patch("time.sleep"), \
             mock.patch("json.load", return_value={"ok": True}):
            result = self.bot.api_call("getUpdates", offset=0)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["n"], 3)

    def test_persistent_network_error_raises_after_retries(self):
        def always_fails(req, timeout=40):
            raise urllib.error.URLError("Network is unreachable")

        with mock.patch("urllib.request.urlopen", side_effect=always_fails), \
             mock.patch("time.sleep"):
            with self.assertRaises(urllib.error.URLError):
                self.bot.api_call("getUpdates", offset=0)

    def test_remote_disconnected_is_retried_and_succeeds(self):
        # Live incident 2026-10-20T18:18 run: getUpdates raised
        # http.client.RemoteDisconnected, which escaped this retry loop
        # uncaught (only urllib.error.URLError/TimeoutError were caught) and
        # killed the whole 5h50m long-poll process, leaving the bot unable
        # to answer pre_checkout_query until the next cron slot picked it up.
        calls = {"n": 0}

        def flaky_urlopen(req, timeout=40):
            calls["n"] += 1
            if calls["n"] < 3:
                raise http.client.RemoteDisconnected(
                    "Remote end closed connection without response"
                )
            return mock.mock_open(read_data=b'{"ok": true}')()

        with mock.patch("urllib.request.urlopen", side_effect=flaky_urlopen), \
             mock.patch("time.sleep"), \
             mock.patch("json.load", return_value={"ok": True}):
            result = self.bot.api_call("getUpdates", offset=0)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["n"], 3)

    def test_main_loop_backs_off_on_network_error_instead_of_crashing(self):
        self.bot.STATE_PATH = tempfile.mktemp()
        self.bot.FULFILLED_PATH = tempfile.mktemp()
        self.bot.PENDING_PATH = tempfile.mktemp()
        calls = {"n": 0}

        def fake_api_call(method, **params):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError("Network is unreachable")
            return {"ok": True, "result": []}

        # First value is consumed by prune_pending()'s time.time() call before
        # the deadline is even computed; deadline = second value + MAX_RUNTIME_SEC;
        # loop re-checks time.time() on every iteration, so give exactly enough
        # values to run twice then stop.
        with mock.patch.object(self.bot, "api_call", fake_api_call), \
             mock.patch.object(self.bot, "MAX_RUNTIME_SEC", 1), \
             mock.patch("time.sleep"), \
             mock.patch("time.time", side_effect=[0, 0, 0, 0.5, 2]):
            self.bot.main()
        self.assertEqual(calls["n"], 2)

    def test_main_loop_backs_off_on_remote_disconnected_instead_of_crashing(self):
        # Same scenario as the URLError test above, but for the exception
        # type that actually crashed the live 2026-10-20T18:18 run uncaught
        # (http.client.RemoteDisconnected is not a urllib.error.URLError).
        self.bot.STATE_PATH = tempfile.mktemp()
        self.bot.FULFILLED_PATH = tempfile.mktemp()
        self.bot.PENDING_PATH = tempfile.mktemp()
        calls = {"n": 0}

        def fake_api_call(method, **params):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http.client.RemoteDisconnected(
                    "Remote end closed connection without response"
                )
            return {"ok": True, "result": []}

        with mock.patch.object(self.bot, "api_call", fake_api_call), \
             mock.patch.object(self.bot, "MAX_RUNTIME_SEC", 1), \
             mock.patch("time.sleep"), \
             mock.patch("time.time", side_effect=[0, 0, 0, 0.5, 2]):
            self.bot.main()
        self.assertEqual(calls["n"], 2)


class TestCustomInvoiceFlow(unittest.TestCase):
    """End-to-end for the new paid custom-invoice flow: a plain-text /invoice
    message -> a pending order + pay link -> pre_checkout accepted only while
    the order is pending -> successful_payment renders and sends the file."""

    GOOD_TEXT = (
        "/invoice\n"
        "from: Jane Doe\n"
        "to: Acme Corp\n"
        "item: Website redesign | 10 | 50"
    )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        import bot
        self.bot = bot
        self.bot.STATE_PATH = os.path.join(self.tmpdir, "state.json")
        self.bot.FULFILLED_PATH = os.path.join(self.tmpdir, "fulfilled.json")
        self.bot.PENDING_PATH = os.path.join(self.tmpdir, "pending_orders.json")
        self.calls = []

        def fake_api_call(method, **params):
            self.calls.append((method, params))
            if method == "createInvoiceLink":
                return {"ok": True, "result": "https://t.me/invoice/fakehash"}
            return {"ok": True, "result": True}

        self.api_patcher = mock.patch.object(self.bot, "api_call", fake_api_call)
        self.api_patcher.start()
        self.docs_sent = []
        self.docs_patcher = mock.patch.object(
            self.bot, "send_document",
            side_effect=lambda chat_id, filename, content_bytes, caption=None:
                self.docs_sent.append((chat_id, filename, content_bytes, caption)),
        )
        self.docs_patcher.start()

    def tearDown(self):
        self.api_patcher.stop()
        self.docs_patcher.stop()

    def test_valid_invoice_message_creates_pending_order_and_pay_link(self):
        pending = {}
        self.bot.handle_message({"chat": {"id": 11}, "text": self.GOOD_TEXT}, pending)
        self.assertEqual(len(pending), 1)
        order_id = next(iter(pending))
        self.assertEqual(pending[order_id]["chat_id"], 11)
        method, params = self.calls[-1]
        self.assertEqual(method, "sendMessage")
        self.assertIn("fakehash", params["text"])

    def test_invalid_invoice_message_sends_error_not_pending_order(self):
        pending = {}
        self.bot.handle_message({"chat": {"id": 11}, "text": "/invoice\nfrom: Jane"}, pending)
        self.assertEqual(pending, {})
        method, params = self.calls[-1]
        self.assertEqual(method, "sendMessage")
        self.assertIn("item", params["text"].lower())

    def test_bare_invoice_command_sends_usage(self):
        pending = {}
        self.bot.handle_message({"chat": {"id": 11}, "text": "/invoice"}, pending)
        self.assertEqual(pending, {})
        method, params = self.calls[-1]
        self.assertIn("item", params["text"])

    def test_unrelated_message_is_ignored(self):
        pending = {}
        self.bot.handle_message({"chat": {"id": 11}, "text": "hello there"}, pending)
        self.assertEqual(pending, {})
        self.assertEqual(self.calls, [])

    def test_message_without_text_is_ignored(self):
        pending = {}
        self.bot.handle_message({"chat": {"id": 11}, "sticker": {}}, pending)
        self.assertEqual(self.calls, [])

    def test_pre_checkout_accepted_only_while_order_pending(self):
        pending = {"abcd1234": {"chat_id": 11, "data": {}, "ts": time.time()}}
        self.bot.handle_pre_checkout({"id": "pcq1", "invoice_payload": "custom-invoice:abcd1234"}, pending)
        method, params = self.calls[-1]
        self.assertTrue(params["ok"])

    def test_pre_checkout_rejected_for_unknown_order_id(self):
        pending = {}
        self.bot.handle_pre_checkout({"id": "pcq1", "invoice_payload": "custom-invoice:ghost"}, pending)
        method, params = self.calls[-1]
        self.assertFalse(params["ok"])

    def test_full_flow_message_to_delivered_file(self):
        pending = {}
        self.bot.handle_message({"chat": {"id": 11}, "text": self.GOOD_TEXT}, pending)
        order_id = next(iter(pending))
        fulfilled = {}
        message = {
            "chat": {"id": 11},
            "successful_payment": {
                "telegram_payment_charge_id": "chX",
                "invoice_payload": f"custom-invoice:{order_id}",
                "total_amount": 100,
            },
        }
        self.bot.handle_successful_payment(message, fulfilled, pending)
        self.assertEqual(pending, {})  # consumed
        self.assertIn("chX", fulfilled)
        self.assertEqual(len(self.docs_sent), 1)
        chat_id, filename, content_bytes, caption = self.docs_sent[0]
        self.assertEqual(chat_id, 11)
        self.assertIn(b"Jane Doe", content_bytes)
        self.assertIn(b"$500.00", content_bytes)  # 10 * 50

    def test_payment_for_missing_pending_order_notifies_human_not_crash(self):
        notified = []
        fulfilled = {}
        message = {
            "chat": {"id": 11},
            "successful_payment": {
                "telegram_payment_charge_id": "chY",
                "invoice_payload": "custom-invoice:ghost",
                "total_amount": 100,
            },
        }
        with mock.patch.object(self.bot, "notify_human", side_effect=lambda t: notified.append(t)):
            self.bot.handle_successful_payment(message, fulfilled, {})
        self.assertEqual(len(notified), 1)
        self.assertIn("URGENT", notified[0])
        self.assertEqual(self.docs_sent, [])
        self.assertNotIn("chY", fulfilled)

    def test_prune_pending_drops_old_orders_keeps_fresh(self):
        now = time.time()
        pending = {
            "old": {"chat_id": 1, "data": {}, "ts": now - self.bot.PENDING_TTL_SEC - 10},
            "fresh": {"chat_id": 2, "data": {}, "ts": now - 10},
        }
        self.bot.prune_pending(pending)
        self.assertEqual(set(pending), {"fresh"})


class TestSubscriptionFlow(unittest.TestCase):
    """Recurring Stars subscription: /subscribe -> createInvoiceLink with
    subscription_period -> successful_payment (first + renewal) -> /invoice
    delivers free while active -> /status reports state -> /cancel stops
    renewal without revoking the current period."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        import bot
        self.bot = bot
        self.bot.STATE_PATH = os.path.join(self.tmpdir, "state.json")
        self.bot.FULFILLED_PATH = os.path.join(self.tmpdir, "fulfilled.json")
        self.bot.PENDING_PATH = os.path.join(self.tmpdir, "pending_orders.json")
        self.bot.SUBSCRIBERS_PATH = os.path.join(self.tmpdir, "subscribers.json")
        self.calls = []

        def fake_api_call(method, **params):
            self.calls.append((method, params))
            if method == "createInvoiceLink":
                return {"ok": True, "result": "https://t.me/invoice/subhash"}
            return {"ok": True, "result": True}

        self.api_patcher = mock.patch.object(self.bot, "api_call", fake_api_call)
        self.api_patcher.start()
        self.docs_sent = []
        self.docs_patcher = mock.patch.object(
            self.bot, "send_document",
            side_effect=lambda chat_id, filename, content_bytes, caption=None:
                self.docs_sent.append((chat_id, filename, content_bytes, caption)),
        )
        self.docs_patcher.start()

    def tearDown(self):
        self.api_patcher.stop()
        self.docs_patcher.stop()

    def test_subscribe_creates_invoice_link_with_subscription_period(self):
        self.bot.handle_message({"chat": {"id": 1}, "text": "/subscribe"}, {}, {})
        method, params = next(c for c in self.calls if c[0] == "createInvoiceLink")
        self.assertEqual(params["currency"], "XTR")
        self.assertEqual(params["subscription_period"], self.bot.SUBSCRIPTION_PERIOD_SEC)
        self.assertEqual(len(params["prices"]), 1)
        self.assertEqual(params["payload"], self.bot.SUBSCRIPTION_PAYLOAD)

    def test_subscribe_when_already_active_does_not_create_new_link(self):
        subscribers = {"1": {"user_id": 1, "charge_id": "c1", "expires": time.time() + 1000, "is_recurring": True}}
        self.bot.handle_message({"chat": {"id": 1}, "text": "/subscribe"}, {}, subscribers)
        self.assertFalse(any(c[0] == "createInvoiceLink" for c in self.calls))

    def test_pre_checkout_accepts_subscription_payload(self):
        self.bot.handle_pre_checkout({"id": "pcq1", "invoice_payload": "unlimited-invoices-sub"}, {})
        method, params = self.calls[-1]
        self.assertTrue(params["ok"])

    def test_first_successful_payment_creates_subscriber_and_notifies_new(self):
        fulfilled, subscribers = {}, {}
        expires = time.time() + self.bot.SUBSCRIPTION_PERIOD_SEC
        message = {
            "chat": {"id": 5}, "from": {"id": 5},
            "successful_payment": {
                "telegram_payment_charge_id": "sub-ch1",
                "invoice_payload": "unlimited-invoices-sub",
                "total_amount": self.bot.SUBSCRIPTION_STARS,
                "subscription_expiration_date": expires,
                "is_first_recurring": True,
            },
        }
        notified = []
        with mock.patch.object(self.bot, "notify_human", side_effect=lambda t: notified.append(t)):
            self.bot.handle_successful_payment(message, fulfilled, {}, subscribers)
        self.assertIn("5", subscribers)
        self.assertEqual(subscribers["5"]["expires"], expires)
        self.assertIn("sub-ch1", fulfilled)
        self.assertIn("New", notified[0])

    def test_renewal_payment_extends_expiration_and_notifies_renewed(self):
        fulfilled = {}
        old_expires = time.time() + 100
        subscribers = {"5": {"user_id": 5, "charge_id": "sub-ch1", "expires": old_expires, "is_recurring": True}}
        new_expires = old_expires + self.bot.SUBSCRIPTION_PERIOD_SEC
        message = {
            "chat": {"id": 5}, "from": {"id": 5},
            "successful_payment": {
                "telegram_payment_charge_id": "sub-ch2",
                "invoice_payload": "unlimited-invoices-sub",
                "total_amount": self.bot.SUBSCRIPTION_STARS,
                "subscription_expiration_date": new_expires,
                "is_first_recurring": False,
            },
        }
        notified = []
        with mock.patch.object(self.bot, "notify_human", side_effect=lambda t: notified.append(t)):
            self.bot.handle_successful_payment(message, fulfilled, {}, subscribers)
        self.assertEqual(subscribers["5"]["expires"], new_expires)
        self.assertIn("Renewed", notified[0])

    def test_replayed_update_for_same_period_is_not_double_processed(self):
        fulfilled = {}
        expires = time.time() + self.bot.SUBSCRIPTION_PERIOD_SEC
        subscribers = {"5": {"user_id": 5, "charge_id": "sub-ch1", "expires": expires, "is_recurring": True}}
        message = {
            "chat": {"id": 5}, "from": {"id": 5},
            "successful_payment": {
                "telegram_payment_charge_id": "sub-ch1",
                "invoice_payload": "unlimited-invoices-sub",
                "total_amount": self.bot.SUBSCRIPTION_STARS,
                "subscription_expiration_date": expires,
                "is_first_recurring": True,
            },
        }
        before = len(self.calls)
        self.bot.handle_successful_payment(message, fulfilled, {}, subscribers)
        self.assertEqual(len(self.calls), before)  # no sendMessage fired again

    def test_active_subscriber_gets_invoice_for_free_no_payment_link(self):
        subscribers = {"7": {"user_id": 7, "charge_id": "c1", "expires": time.time() + 1000, "is_recurring": True}}
        text = "/invoice\nfrom: Jane\nto: Acme\nitem: Design | 1 | 100"
        self.bot.handle_message({"chat": {"id": 7}, "text": text}, {}, subscribers)
        self.assertEqual(len(self.docs_sent), 1)
        self.assertFalse(any(c[0] == "createInvoiceLink" for c in self.calls))

    def test_non_subscriber_still_gets_payment_link_for_invoice(self):
        text = "/invoice\nfrom: Jane\nto: Acme\nitem: Design | 1 | 100"
        pending = {}
        self.bot.handle_message({"chat": {"id": 8}, "text": text}, pending, {})
        self.assertEqual(self.docs_sent, [])
        self.assertTrue(any(c[0] == "createInvoiceLink" for c in self.calls))
        self.assertEqual(len(pending), 1)

    def test_status_reports_active_and_inactive(self):
        subscribers = {"1": {"user_id": 1, "charge_id": "c1", "expires": time.time() + 1000, "is_recurring": True}}
        self.bot.handle_message({"chat": {"id": 1}, "text": "/status"}, {}, subscribers)
        self.assertIn("Active subscription", self.calls[-1][1]["text"])

        self.bot.handle_message({"chat": {"id": 2}, "text": "/status"}, {}, {})
        self.assertIn("No active subscription", self.calls[-1][1]["text"])

    def test_cancel_calls_edit_user_star_subscription_and_keeps_access(self):
        expires = time.time() + 1000
        subscribers = {"3": {"user_id": 3, "charge_id": "c1", "expires": expires, "is_recurring": True}}
        self.bot.handle_message({"chat": {"id": 3}, "text": "/cancel"}, {}, subscribers)
        method, params = next(c for c in self.calls if c[0] == "editUserStarSubscription")
        self.assertEqual(params["user_id"], 3)
        self.assertEqual(params["telegram_payment_charge_id"], "c1")
        self.assertTrue(params["is_canceled"])
        self.assertFalse(subscribers["3"]["is_recurring"])
        self.assertEqual(subscribers["3"]["expires"], expires)  # current period untouched

    def test_cancel_with_no_subscription_does_not_call_api(self):
        self.bot.handle_message({"chat": {"id": 9}, "text": "/cancel"}, {}, {})
        self.assertFalse(any(c[0] == "editUserStarSubscription" for c in self.calls))

    def test_is_active_subscriber_checks_expiry_not_recurring_flag(self):
        subs = {"1": {"expires": time.time() + 10, "is_recurring": False}}
        self.assertTrue(self.bot.is_active_subscriber(subs, 1))
        subs2 = {"1": {"expires": time.time() - 10, "is_recurring": True}}
        self.assertFalse(self.bot.is_active_subscriber(subs2, 1))
        self.assertFalse(self.bot.is_active_subscriber({}, 999))


if __name__ == "__main__":
    unittest.main()

