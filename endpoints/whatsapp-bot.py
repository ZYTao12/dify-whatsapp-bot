import json
from typing import Mapping, Optional
import requests
from werkzeug import Request, Response
from dify_plugin import Endpoint
from dify_plugin.invocations.app.chat import ChatAppInvocation


class WhatsappBotEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        """
        WhatsApp Cloud API webhook endpoint (POST only).

        - POST: Handle incoming messages and (optionally) reply via Dify App + WhatsApp API
        """
        if r.method == 'GET':
            return self._handle_verify(r, settings)
        if r.method != 'POST':
            return Response("Method Not Allowed", status=405, content_type="text/plain")

        return self._handle_webhook(r, settings)

    def _handle_verify(self, r: Request, settings: Mapping) -> Response:
        """Handle Meta webhook verification by echoing hub.challenge when token matches."""
        try:
            # werkzeug Request.args provides query parameters
            args = r.args or {}
            mode = (args.get('hub.mode') or args.get('mode') or '').strip()
            token = (args.get('hub.verify_token') or args.get('verify_token') or '').strip()
            challenge = args.get('hub.challenge')

            expected_token = (settings.get('verify_token') or '').strip()

            if mode == 'subscribe' and expected_token and token == expected_token and challenge is not None:
                # Return the hub.challenge as plain text (no JSON), as required by Meta
                return Response(str(challenge), status=200, content_type='text/plain')

            return Response('Forbidden', status=403, content_type='text/plain')
        except Exception:
            # Do not leak details; simply forbid on errors
            return Response('Forbidden', status=403, content_type='text/plain')

    def _extract_text(self, message: Mapping) -> Optional[str]:
        message_type = message.get('type')
        if message_type == 'text':
            text = message.get('text') or {}
            return text.get('body')
        # Add more types handling in future (interactive, button, etc.)
        return None

    def _get_app_id(self, app_setting) -> Optional[str]:
        """Extract app_id from app selector which may be a dict or string."""
        if not app_setting:
            return None
        if isinstance(app_setting, str):
            app_id = app_setting.strip()
            return app_id or None
        if isinstance(app_setting, Mapping):
            app_id = (app_setting.get('app_id') or app_setting.get('id') or '').strip()  # type: ignore
            return app_id or None
        return None

    def _invoke_app_reply(
        self,
        *,
        app_id: str,
        query: str,
        identify_inputs: Mapping,
        conversation_key: str,
    ) -> Optional[str]:
        """Invoke Dify app with conversation continuity and return answer text if any."""
        try:
            conversation_id: Optional[str] = None
            try:
                raw = self.session.storage.get(conversation_key)
                if raw:
                    conversation_id = raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else str(raw)
            except Exception:
                conversation_id = None

            invoke_params = {
                'app_id': app_id,
                'query': query,
                'inputs': dict(identify_inputs),
                'response_mode': 'blocking',
            }
            if conversation_id:
                invoke_params['conversation_id'] = conversation_id

            # Prefer using the session app invocation to align with other bots
            result = self.session.app.chat.invoke(**invoke_params)

            answer = (
                result.get('answer')
                or result.get('output_text')
                or result.get('message')
            )

            new_conversation_id = result.get('conversation_id')
            if new_conversation_id:
                try:
                    self.session.storage.set(conversation_key, str(new_conversation_id).encode('utf-8'))
                except Exception:
                    pass

            return str(answer) if answer is not None else None
        except Exception:
            return None

    def _send_whatsapp_text(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        to_wa_id: str,
        body_text: str,
    ) -> None:
        url = f"https://graph.facebook.com/v24.0/{phone_number_id}/messages"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }
        data = {
            'messaging_product': 'whatsapp',
            'to': to_wa_id,
            'type': 'text',
            'text': {'body': body_text},
        }
        try:
            # Send without raising exceptions; rely on platform logs for failures
            requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
        except Exception:
            pass

    def _ok(self) -> Response:
        return Response("ok", status=200, content_type="text/plain")

    def _handle_webhook(self, r: Request, settings: Mapping) -> Response:
        try:
            payload = r.get_json(silent=True) or {}
        except Exception:
            return Response("Bad Request", status=400, content_type="text/plain")

        access_token = (settings.get('access_token') or '').strip()
        phone_number_id = (settings.get('phone_number_id') or '').strip()
        app_id = self._get_app_id(settings.get('app'))

        if not payload or 'entry' not in payload:
            return self._ok()

        can_reply = bool(access_token and phone_number_id)

        try:
            for entry in payload.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value') or {}
                    messages = value.get('messages') or []
                    for message in messages:
                        sender_wa_id = message.get('from')
                        text_body = self._extract_text(message)
                        if not sender_wa_id or text_body is None:
                            continue

                        # Prepare identification inputs for the app
                        identify_inputs = {
                            'whatsapp_user_id': sender_wa_id,
                            'phone_number_id': phone_number_id,
                        }

                        # Build a stable conversation storage key scoped to this phone number
                        conversation_key = f"whatsapp:{phone_number_id}:{sender_wa_id}"

                        reply_text = None
                        if app_id:
                            reply_text = self._invoke_app_reply(
                                app_id=app_id,
                                query=text_body,
                                identify_inputs=identify_inputs,
                                conversation_key=conversation_key,
                            )
                        # Fallback to echo if no app configured or invocation failed
                        if not reply_text:
                            reply_text = text_body

                        if can_reply and reply_text:
                            self._send_whatsapp_text(
                                access_token=access_token,
                                phone_number_id=phone_number_id,
                                to_wa_id=sender_wa_id,
                                body_text=reply_text,
                            )
        except Exception:
            # Swallow errors to avoid webhook retries
            return self._ok()

        return self._ok()
