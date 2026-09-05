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
                "LEGAL_API_TOKEN": "test-legal-token",
                "LEGAL_DOCUMENTS_DIR": os.path.join(self.tempdir.name, "legal-documents"),
                "LEGAL_UPLOAD_DIR": os.path.join(self.tempdir.name, "legal-uploads"),
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

        import legal_routes
        import main

        importlib.reload(legal_routes)
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

    def test_legal_document_api_requires_token_and_generates_text_document(self):
        unauthorized = self.client.post(
            "/api/legal/generate",
            json={
                "template_id": "nda",
                "form_data": {"party_name": "Acme"},
                "format": "txt",
                "filename": "acme-nda",
            },
        )
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.post(
            "/api/legal/generate",
            json={
                "template_id": "nda",
                "form_data": {"party_name": "Acme"},
                "format": "txt",
                "filename": "acme-nda",
            },
            headers={"Authorization": "Bearer test-legal-token"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["file"], "acme-nda.txt")

    def test_legal_document_workspace_requires_login_and_uses_admin_session(self):
        unauthorized = self.client.get("/admin/legal-documents", follow_redirects=False)
        self.assertEqual(unauthorized.status_code, 303)
        self.assertIn("/login?error=Authentication+Required", unauthorized.headers["location"])

        self.client.cookies.set("session_token", self.main.SESSION_SECRET)
        page = self.client.get("/admin/legal-documents")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Legal Document Business Writer", page.text)

        response = self.client.post(
            "/api/legal/generate",
            json={
                "template_id": "nda",
                "form_data": {"party_name": "Acme"},
                "format": "txt",
                "filename": "session-nda",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["file"], "session-nda.txt")

    def test_legacy_profiles_schema_is_migrated_without_data_loss(self):
        legacy_path = os.path.join(self.tempdir.name, "legacy.db")
        with sqlite3.connect(legacy_path) as conn:
            conn.execute(
                """
                CREATE TABLE profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT UNIQUE NOT NULL,
                    credit_risk_rating TEXT,
                    annual_revenue REAL
                )
                """
            )
            conn.execute(
                "INSERT INTO profiles (company_name) VALUES (?)", ("Existing business",)
            )

        with patch.object(self.main, "DATABASE_PATH", legacy_path):
            self.main.init_db()
            with sqlite3.connect(legacy_path) as conn:
                row = conn.execute(
                    "SELECT company_name, created_at FROM profiles"
                ).fetchone()

        self.assertEqual(row[0], "Existing business")
        self.assertIsNotNone(row[1])

    def test_portal_otp_survives_application_reload(self):
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

    @patch("main.scan_public_signals")
    def test_signal_scan_stores_discovered_public_signals(self, mock_scan):
        from business_signals import BusinessSignal

        mock_scan.return_value = [
            BusinessSignal(
                business_name="Growing Co",
                signal_type="news",
                signal_summary="Growing Co opens a new location",
                source_url="https://example.com/growing-co",
                source_name="Example News",
                location="Chesapeake, VA",
                confidence_score=0.8,
            )
        ]
        self.client.cookies.set("session_token", self.main.SESSION_SECRET)

        response = self.client.post(
            "/api/signals/scan",
            data={"location": "Chesapeake, VA", "industry": "construction"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["signals_stored"], 1)
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM business_signals").fetchone()[0]
        self.assertEqual(count, 1)

    def test_youcom_mcp_response_parses_current_news(self):
        from business_signals import YouComSignalScanner

        result = YouComSignalScanner._parse_mcp_response(
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{}"}]}}\n'
        )

        self.assertEqual(result["content"][0]["type"], "text")

    def test_youcom_mcp_response_rejects_missing_result(self):
        from business_signals import YouComSignalScanner

        with self.assertRaisesRegex(RuntimeError, "no result"):
            YouComSignalScanner._parse_mcp_response(
                'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/message"}\n'
            )

    def test_youcom_scanner_reads_live_news_and_web_sections(self):
        from business_signals import YouComSignalScanner

        signals = YouComSignalScanner._signals_from_results(
            {
                "news": [{
                    "title": "News Co expands in Norfolk, VA",
                    "url": "https://news.example",
                    "description": "News Co announces a Norfolk, VA expansion.",
                }],
                "web": [{
                    "title": "Web Co opens a Norfolk, VA location",
                    "url": "https://web.example",
                    "description": "Web Co has a new location in Norfolk, VA.",
                }],
                "web_irrelevant": [{"title": "Ignored", "url": "https://ignored.example"}],
            },
            "Norfolk, VA",
        )

        self.assertEqual([signal.source_name for signal in signals], [
            "You.com live news", "You.com live web"
        ])

    @patch("email_notifier.urllib.request.urlopen")
    def test_agentmail_sends_email_otp_over_https(self, mock_urlopen):
        import email_notifier

        response = Mock(status=200)
        mock_urlopen.return_value.__enter__.return_value = response
        with patch.multiple(
            email_notifier,
            AGENTMAIL_API_KEY="agentmail-key",
            AGENTMAIL_INBOX_ID="inbox-id",
        ):
            self.assertTrue(
                email_notifier.send_portal_login_code("customer@example.com", "123456")
            )

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://api.agentmail.to/v0/inboxes/inbox-id/messages/send",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer agentmail-key")
        self.assertIn(b"123456", request.data)

    @patch("main.stripe.StripeClient")
    def test_billing_portal_creates_stripe_customer_for_portal_signup(
        self, mock_stripe_client_cls
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            customer_id = self.main.provision_customer_from_checkout(
                conn,
                email="buyer@example.com",
                business_name="Buyer Co",
                stripe_customer_id=None,
            )
            session_token = self.main.create_portal_session_token(conn, customer_id)

        stripe_client = Mock()
        stripe_client.customers.create.return_value = {"id": "cus_portal_123"}
        stripe_client.billing_portal.sessions.create.return_value = {
            "url": "https://billing.stripe.com/session/test"
        }
        mock_stripe_client_cls.return_value = stripe_client
        self.client.cookies.set("portal_session_token", session_token)

        response = self.client.post("/portal/billing", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"], "https://billing.stripe.com/session/test"
        )
        self.assertEqual(
            stripe_client.customers.create.call_args.kwargs["params"]["email"],
            "buyer@example.com",
        )
        portal_params = stripe_client.billing_portal.sessions.create.call_args.kwargs[
            "params"
        ]
        self.assertEqual(portal_params["customer"], "cus_portal_123")
        with sqlite3.connect(self.db_path) as conn:
            customer = conn.execute(
                "SELECT stripe_customer_id FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
        self.assertEqual(customer[0], "cus_portal_123")

    def test_aws_sms_delivery_is_used_for_portal_login_codes(self):
        self.main.aws_otp_configured = Mock(return_value=True)
        self.main.send_sns_sms = Mock(return_value=True)
        self.client.post(
            "/signup",
            data={
                "business_name": "Acme",
                "phone": "+15551234567",
                "consent": "accepted",
            },
        )

        response = self.client.post(
            "/portal/request-code",
            data={"channel": "phone", "identifier": "+15551234567"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("We texted you a 6-digit code", response.text)
        self.main.send_sns_sms.assert_called_once()

    @patch("aws_messaging._client")
    def test_aws_messaging_sends_ses_email_and_sns_sms(self, mock_client):
        import aws_messaging

        client = Mock()
        mock_client.return_value = client
        with patch.multiple(
            aws_messaging,
            AWS_OTP_ENABLED=True,
            AWS_REGION="us-east-1",
            AWS_ACCESS_KEY_ID="access-key",
            AWS_SECRET_ACCESS_KEY="secret-key",
            AWS_SES_FROM_EMAIL="no-reply@example.com",
            AWS_SNS_SENDER_ID="BizStack",
        ):
            self.assertTrue(
                aws_messaging.send_email(
                    "buyer@example.com", "Your code", "Your code is 123456"
                )
            )
            self.assertTrue(aws_messaging.send_sms("+15551234567", "Your code is 123456"))

        client.send_email.assert_called_once()
        publish = client.publish.call_args.kwargs
        self.assertEqual(publish["PhoneNumber"], "+15551234567")
        self.assertEqual(
            publish["MessageAttributes"]["AWS.SNS.SMS.SMSType"]["StringValue"],
            "Transactional",
        )
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

    def test_admin_can_register_an_official_public_rate_source(self):
        self.client.cookies.set("session_token", self.main.SESSION_SECRET)

        response = self.client.post(
            "/api/public-bank-rate-sources",
            data={
                "bank_name": "Example Bank",
                "product_name": "Business line of credit",
                "region": "VA",
                "source_url": "https://www.examplebank.test/rates",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Example Bank", response.text)
        with sqlite3.connect(self.db_path) as conn:
            source = conn.execute(
                "SELECT bank_name, source_url FROM public_bank_rate_sources"
            ).fetchone()
        self.assertEqual(source, ("Example Bank", "https://www.examplebank.test/rates"))

    @patch("main.discover_live_public_bank_rates")
    def test_admin_can_run_one_click_live_public_rate_scan(self, mock_discover):
        mock_discover.return_value = [{
            "bank_name": "Example Bank business loan rates",
            "product_name": "business loan",
            "region": "VA",
            "source_url": "https://www.examplebank.test/business-loans",
            "source_summary": "Business loans from 6.5% APR.",
            "observed_rate": 6.5,
            "rate_kind": "APR",
            "source_domain": "examplebank.test",
            "discovered_at": "2026-09-02T00:00:00+00:00",
        }]
        self.client.cookies.set("session_token", self.main.SESSION_SECRET)

        response = self.client.post("/api/public-bank-rate-sources/scan")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rates"][0]["observed_rate"], 6.5)
        with sqlite3.connect(self.db_path) as conn:
            stored = conn.execute(
                "SELECT observed_rate, source_url FROM live_public_bank_rates"
            ).fetchone()
        self.assertEqual(stored, (6.5, "https://www.examplebank.test/business-loans"))

    @patch("main.scan_public_signals")
    @patch("main.discover_live_public_bank_rates")
    @patch("main.discover_public_business_contact", return_value="contact@growing.example")
    @patch("email_notifier.send_email")
    @patch("email_notifier.email_configured", return_value=True)
    def test_one_click_campaign_sends_to_matching_opted_in_business(
        self, _mock_configured, mock_send, _mock_contact, mock_discover, mock_signals
    ):
        from business_signals import BusinessSignal

        mock_discover.return_value = []
        mock_signals.return_value = [BusinessSignal(
            business_name="Growing Co",
            signal_type="news",
            signal_summary="Growing Co expands in Norfolk, VA",
            source_url="https://news.example/growing-co",
            source_name="Example News",
            location="Norfolk, VA",
        )]
        mock_send.return_value = True
        self.client.cookies.set("session_token", self.main.SESSION_SECRET)
        self.main.SENDER_PHYSICAL_ADDRESS = "123 Main Street, Norfolk, VA 23510"
        response = self.client.post("/api/automation/run")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["emails_sent"][0]["email"], "contact@growing.example")
        mock_send.assert_called_once()

    def test_public_rate_monitor_extracts_labeled_rates_and_same_site_links(self):
        from public_rate_sources import _rate_for_product, _rate_from_text, _rate_page_links

        self.assertEqual(_rate_from_text("Fixed rate of 7.25% APR"), (7.25, "APR"))
        self.assertEqual(_rate_from_text("Borrow from 7.25% today"), (None, None))
        self.assertEqual(
            _rate_for_product(
                "Business loan rates begin at 7.25% APR for qualified borrowers.",
                "business loan",
            ),
            (7.25, "APR"),
        )
        self.assertEqual(
            _rate_for_product(
                "Business loans are available. Savings account earns 4.25% APY.",
                "business loan",
            ),
            (None, None),
        )
        self.assertEqual(
            _rate_page_links(
                '<a href="/business-loans">Business loans</a>'
                '<a href="https://other.example/rates">Other</a>',
                "https://bank.example/",
            ),
            ["https://bank.example/business-loans"],
        )

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
        self.assertEqual(fallback_params["line_items"][0]["price_data"]["unit_amount"], 9900)

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
