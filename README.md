## whatsapp-bot

**Author:** langgenius
**Version:** 0.0.1
**Type:** extension

### Description
WhatsApp Cloud API extension that receives messages via webhook and optionally forwards them to a selected Dify App to generate replies, then sends responses back to WhatsApp users.

### Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run locally for remote debug in Dify:
   ```bash
   python -m main
   ```

### Configure in Dify

Open plugin settings and fill:
- `WhatsApp Access Token` (`access_token`): from Meta for Developers.
- `Webhook Verify Token` (`verify_token`): choose a secret and reuse in Meta webhook setup.
- `Phone Number ID` (`phone_number_id`): WhatsApp Business phone number ID.
- `Dify App` (`app`): optional. If set, incoming user text can be forwarded to the app; otherwise the plugin echoes the text.

### Webhook Endpoints

- `GET /webhooks/whatsapp` for webhook verification: responds with `hub.challenge` when `hub.verify_token` matches settings.
- `POST /webhooks/whatsapp` to receive events. Currently extracts text messages and replies with text.

### Notes

- This plugin currently echoes user text. Integrate Dify App invocation inside `_generate_reply` when app reverse-call APIs are available.
- WhatsApp Cloud API docs: `https://developers.facebook.com/docs/whatsapp/cloud-api`
