import json
import re
from collections.abc import Generator
import requests

from dify_plugin.interfaces.tool import Tool
from dify_plugin.entities.tool import ToolInvokeMessage


class SendMessageTool(Tool):
    def _invoke(self, tool_parameters: dict) -> Generator[ToolInvokeMessage, None, None]:
        access_token: str = (self.runtime.credentials.get("access_token") or "").strip()
        phone_number_id: str = (self.runtime.credentials.get("phone_number_id") or "").strip()

        to_raw: str = (tool_parameters.get("to") or "").strip()
        text: str = (tool_parameters.get("text") or "").strip()

        if not access_token or not phone_number_id:
            yield self.create_log_message(
                label="credentials",
                data={
                    "error": "Missing credentials",
                    "have_access_token": bool(access_token),
                    "have_phone_number_id": bool(phone_number_id),
                },
                status=ToolInvokeMessage.LogMessage.LogStatus.ERROR,
            )
            yield self.create_text_message("Configuration error: missing WhatsApp credentials")
            return

        if not to_raw or not text:
            yield self.create_log_message(
                label="parameters",
                data={"error": "Missing required parameters", "to": bool(to_raw), "text": bool(text)},
                status=ToolInvokeMessage.LogMessage.LogStatus.ERROR,
            )
            yield self.create_text_message("Missing required parameters: to, text")
            return

        # Normalize recipient: WhatsApp Cloud API expects full international number without '+'
        to = re.sub(r"[^0-9]", "", to_raw)

        url = f"https://graph.facebook.com/v24.0/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

        yield self.create_log_message(label="send_request", data={"url": url, "to": to})

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            body = safe_json(resp)
            ok = 200 <= resp.status_code < 300

            status = (
                ToolInvokeMessage.LogMessage.LogStatus.SUCCESS
                if ok
                else ToolInvokeMessage.LogMessage.LogStatus.ERROR
            )
            yield self.create_log_message(
                label="send_response",
                data={"status_code": resp.status_code, "body": body},
                status=status,
            )

            if ok:
                # Emit structured JSON for programmatic use
                yield self.create_json_message({"result": "sent", "to": to, "response": body})
                # Also emit a simple text as the final output for workflows expecting a string
                try:
                    wa_message_id = None
                    if isinstance(body, dict):
                        msgs = body.get("messages") or []
                        if isinstance(msgs, list) and msgs:
                            wa_message_id = msgs[0].get("id")
                    summary = f"sent to {to}" + (f" (id: {wa_message_id})" if wa_message_id else "")
                except Exception:
                    summary = f"sent to {to}"
                yield self.create_text_message(summary)
                return

            # Provide actionable error feedback
            api_error = extract_api_error(body)
            if api_error:
                hint = suggest_fix(api_error)
                yield self.create_json_message({"error": api_error, "hint": hint})
            else:
                yield self.create_text_message(f"Failed to send message: HTTP {resp.status_code}")

        except Exception as e:
            yield self.create_log_message(
                label="exception",
                data={"error": str(e)},
                status=ToolInvokeMessage.LogMessage.LogStatus.ERROR,
            )
            yield self.create_text_message("Failed to send message due to exception")


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text[:500]}


def extract_api_error(body: dict):
    if isinstance(body, dict) and "error" in body:
        err = body.get("error") or {}
        return {
            "code": err.get("code"),
            "type": err.get("type"),
            "message": err.get("message"),
            "error_subcode": err.get("error_subcode"),
        }
    return None


def suggest_fix(api_error: dict) -> str:
    code = api_error.get("code")
    subcode = api_error.get("error_subcode")
    message = api_error.get("message") or ""

    # Common Graph API errors for WhatsApp Cloud
    if code == 190:
        return "Invalid or expired access token. Recreate a system user token with proper permissions."
    if code == 100:
        return "Invalid parameters. Verify 'phone_number_id' and that 'to' is a valid international number."
    if subcode in {2018049, 131000, 131031}:
        return (
            "Recipient has not messaged your business recently or is not opted-in. "
            "Ensure a recent user-initiated session or use an approved template."
        )
    if "Unsupported post request" in message:
        return "Check that the phone_number_id belongs to your app and Business Account."

    return "Check Business Account setup, permissions (whatsapp_business_messaging), and recipient format (E.164 without '+')."


