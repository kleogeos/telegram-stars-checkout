#!/usr/bin/env python3
"""Offline tests for bot.py's decision logic. Mocks api_call so no network/token
is needed and the real bot (used for human notifications) is never touched."""
import json
import os
import sys
import tempfile
import unittest
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
        self.bot.handle_pre_checkout({"id": "pcq1", "invoice_payload": "starter-kit"})
        method, params = self.calls[-1]
        self.assertEqual(method, "answerPreCheckoutQuery")
        self.assertEqual(params["pre_checkout_query_id"], "pcq1")
        self.assertTrue(params["ok"])
        self.assertNotIn("error_message", params)

    def test_unknown_payload_is_rejected(self):
        self.bot.handle_pre_checkout({"id": "pcq2", "invoice_payload": "typo-product"})
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
        self.bot.handle_pre_checkout({"id": "pcq3", "invoice_payload": "tip-1000"})
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


if __name__ == "__main__":
    unittest.main()
