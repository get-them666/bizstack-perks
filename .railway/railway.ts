import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const bizstackPerksVolume = volume("bizstack-perks-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-east4-eqdc4a", sizeMB: 500 });
  const bizstackPerks = service("bizstack-perks", {
    source: github("get-them666/bizstack-perks", { checkSuites: true }),
    start: "",
    replicas: { "us-east4-eqdc4a": 1 },
    deploy: { drainingSeconds: 0, ipv6EgressEnabled: true, overlapSeconds: 0, preDeployCommand: [], sleepApplication: true },
    domains: ["bizstackperks.com"],
    networking: { privateNetworkEndpoint: "bizstackperks" },
    volumeMounts: { "/app/data": bizstackPerksVolume },
    env: { AUTO_INGEST_OVERRIDE: preserve(), BIZSTACK_ADMIN_PASS: preserve(), BIZSTACK_ADMIN_USER: preserve(), BOT_API_TOKEN: preserve(), BOT_AUTOMATION_ENABLED: preserve(), BOT_AUTO_SEND_EMAIL: preserve(), COMMERCIAL_LENDER_ENDPOINT: preserve(), DOMAIN: preserve(), FINNHUB_DATA_KEY: preserve(), Key: preserve(), PRICE_ID: preserve(), PUBLIC_BASE_URL: preserve(), STRIPE_SECRET_KEY: preserve(), STRIPE_WEBHOOK_SECRET: preserve(), SUPPORT_EMAIL: preserve(), TA_TRACKER_ID: preserve(), TWILIO_ACCOUNT_SID: preserve(), TWILIO_AUTH_TOKEN: preserve(), TWILIO_NUMBER: preserve(), Value: preserve() },
  });

  return project("empowering-youth", {
    resources: [bizstackPerks, bizstackPerksVolume],
  });
});
