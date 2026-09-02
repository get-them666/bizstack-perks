import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


class BizStackPerksAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "test.db")
        self.env_patch = patch.dict(
            os.environ,
            {
                "DATABASE_PATH": self.db_path,
                "BIZSTACK_ADMIN_USER": "admin",
                "BIZSTACK_ADMIN_PASS": "password123",
                "SESSION_COOKIE_SECRET": "test-session-secret",
                "PUBLIC_BASE_URL": "https://example.com",
                "BOT_API_TOKEN": "test-api-token",
                "STRIPE_SECRET_KEY": "sk_test_123",
                "STRIPE_PUBLISHABLE_KEY": "pk_test_123",
                "STRIPE_WEBHOOK_SECRET": "whsec_test_123",
                "PRICE_ID": "price_test_123",
                "TWILIO_ACCOUNT_SID": "AC123456789",
                "TWILIO_AUTH_TOKEN": "auth-token",
                "TWILIO_PHONE_NUMBER": "+15550000000",
            },
            clear=False,
        )
        self.env_patch.start()

        import main

        self.main = importlib.reload(main)
        self.client_manager = TestClient(self.main.app)
        self.client = self.client_manager.__enter__()

    def tearDown(self):
        self.client_manager.__exit__(None, None, None)
        self.env_patch.stop()
        self.tempdir.cleanup()

    def payment_row(self, session_id: str):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT status, customer_email, raw_event_id FROM payments WHERE stripe_session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()

    def call_row(self, call_sid: str):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT call_status, to_number, message FROM call_events WHERE call_sid = ?",
                (call_sid,),
            ).fetchone()
        finally:
            conn.close()

    def test_homepage_renders_checkout_and_voice_ctas(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Start checkout", response.text)
        self.assertIn("/api/checkout/create", response.text)
        self.assertIn("Call or contact", response.text)
        self.assertIn("Frequently asked questions", response.text)

    def test_portal_otp_survives_module_reload_and_is_single_use(self):
        identifier = "customer@example.com"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self.main.init_customer_tables(conn)
            code = self.main.generate_otp(conn, identifier)

        import customer_portal

        reloaded_portal = importlib.reload(customer_portal)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self.assertTrue(reloaded_portal.verify_otp(conn, identifier, code))
            self.assertFalse(reloaded_portal.verify_otp(conn, identifier, code))

    def test_portal_login_explains_when_account_is_not_registered(self):
        response = self.client.post(
            "/portal/request-code",
            data={"channel": "email", "identifier": "unknown@example.com"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("We could not send a code", response.text)
        self.assertIn("Create a free account first", response.text)

    def test_client_registry_is_available_to_authenticated_users(self):
        self.client.cookies.set("session_token", self.main.SESSION_SECRET)
        response = self.client.get("/client")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Client Registry Workspace", response.text)
        self.assertIn("Export CSV", response.text)

    def test_admin_workspace_is_available_to_authenticated_users(self):
        self.client.cookies.set("session_token", self.main.SESSION_SECRET)

        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        self.assertIn("admin workspace", response.text)
        self.assertIn("Opt-in lead requests", response.text)

    @patch("main.stripe.StripeClient")
    def test_checkout_creation_redirects_and_persists_pending_payment(self, mock_stripe_client_cls):
        mock_stripe_client = Mock()
        mock_stripe_client.checkout.sessions.create.return_value = {
            "id": "cs_test_123",
            "url": "https://checkout.stripe.com/pay/cs_test_123",
            "customer": "cus_123",
            "payment_intent": "pi_123",
            "subscription": None,
            "payment_status": "unpaid",
            "status": "open",
            "amount_total": 4900,
            "currency": "usd",
        }
        mock_stripe_client_cls.return_value = mock_stripe_client

        response = self.client.post(
            "/api/checkout/create",
            data={"email": "buyer@example.com", "business_name": "Acme"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "https://checkout.stripe.com/pay/cs_test_123")
        self.assertEqual(self.payment_row("cs_test_123"), ("unpaid", "buyer@example.com", None))
        checkout_params = mock_stripe_client.checkout.sessions.create.call_args.kwargs["params"]
        self.assertEqual(checkout_params["line_items"], [{"price": "price_test_123", "quantity": 1}])
        self.assertNotIn("payment_method_types", checkout_params)
        self.assertNotIn("integration_identifier", checkout_params)

    @patch("main.stripe.StripeClient")
    def test_checkout_uses_configured_item_when_price_id_is_invalid(self, mock_stripe_client_cls):
        mock_stripe_client = Mock()
        mock_stripe_client.checkout.sessions.create.side_effect = [
            self.main.stripe.InvalidRequestError("No such price", "line_items[0]"),
            {
                "id": "cs_test_fallback",
                "url": "https://checkout.stripe.com/pay/cs_test_fallback",
                "customer": None,
                "payment_intent": None,
                "subscription": None,
                "payment_status": "unpaid",
                "status": "open",
                "amount_total": 4900,
                "currency": "usd",
            },
        ]
        mock_stripe_client_cls.return_value = mock_stripe_client

        response = self.client.post(
            "/api/checkout/create",
            data={"business_name": "Acme"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "https://checkout.stripe.com/pay/cs_test_fallback")
        fallback_params = mock_stripe_client.checkout.sessions.create.call_args_list[1].kwargs["params"]
        self.assertEqual(fallback_params["line_items"][0]["price_data"]["unit_amount"], 4900)

    @patch("main.stripe.StripeClient")
    def test_checkout_accepts_stripe_session_objects(self, mock_stripe_client_cls):
        session_data = {
            "id": "cs_test_object",
            "url": "https://checkout.stripe.com/pay/cs_test_object",
            "customer": None,
            "payment_intent": None,
            "subscription": None,
            "payment_status": "unpaid",
            "status": "open",
            "amount_total": 4900,
            "currency": "usd",
        }
        mock_session = Mock()
        mock_session.to_dict.return_value = session_data
        mock_stripe_client = Mock()
        mock_stripe_client.checkout.sessions.create.return_value = mock_session
        mock_stripe_client_cls.return_value = mock_stripe_client

        response = self.client.post("/api/checkout/create", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], session_data["url"])
        self.assertEqual(self.payment_row("cs_test_object"), ("unpaid", None, None))

    @patch("main.stripe.Webhook.construct_event")
    def test_stripe_webhook_marks_completed_checkout_paid(self, mock_construct_event):
        mock_construct_event.return_value = {
            "id": "evt_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_456",
                    "customer": "cus_456",
                    "payment_intent": "pi_456",
                    "subscription": None,
                    "payment_status": "paid",
                    "status": "complete",
                    "amount_total": 4900,
                    "currency": "usd",
                    "customer_details": {"email": "paid@example.com"},
                }
            },
        }

        response = self.client.post(
            "/api/stripe/webhook",
            content=b"{}",
            headers={"stripe-signature": "valid-signature"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.payment_row("cs_test_456"), ("paid", "paid@example.com", "evt_123"))

    def test_twilio_incoming_returns_twiml(self):
        response = self.client.post("/twilio/voice/incoming")

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/xml", response.headers["content-type"])
        self.assertIn("BizStack Perks", response.text)
        self.assertIn("/twilio/voice/process-input", response.text)

    def test_twilio_status_persists_call_events(self):
        validator = self.main.RequestValidator("auth-token")
        callback_data = {
            "CallSid": "CA_status_123",
            "CallStatus": "completed",
            "Direction": "inbound",
            "From": "+15551234567",
            "To": "+15550000000",
        }
        callback_url = "http://testserver/twilio/voice/status"
        signature = validator.compute_signature(callback_url, callback_data)

        response = self.client.post(
            "/twilio/voice/status",
            data=callback_data,
            headers={"X-Twilio-Signature": signature},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.call_row("CA_status_123"), ("completed", "+15550000000", None))

    @patch("main.Client")
    def test_outbound_call_requires_api_key_and_places_call(self, mock_client_cls):
        mock_client = Mock()
        mock_client.calls.create.return_value = Mock(sid="CA_outbound_123", status="queued")
        mock_client_cls.return_value = mock_client

        response = self.client.post(
            "/api/twilio/voice/outbound",
            json={"to_number": "+15551234567", "message": "Hello from BizStack Perks"},
            headers={"X-API-Key": "test-api-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["call_sid"], "CA_outbound_123")
        mock_client.calls.create.assert_called_once()
        self.assertEqual(self.call_row("CA_outbound_123"), ("queued", "+15551234567", "Hello from BizStack Perks"))


if __name__ == "__main__":
    unittest.main()
