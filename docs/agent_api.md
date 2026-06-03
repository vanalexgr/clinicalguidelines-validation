# OpenWebUI Agent API Target

The future agent integration target is an OpenWebUI instance hardcoded to
`https://chat.clinicalguidelines.io`. The Flutter application locks the backend server
(`kServerLockEnabled = true` in `locked_server.dart`), so there is no server picker and the harness
should use that base URL directly.

REST authentication uses an OpenWebUI API key supplied through `CGIO_API_KEY` and sent as
`Authorization: Bearer <key>`. The Flutter app separately obtains a JWT through an in-app OIDC
WebView at `https://chat.clinicalguidelines.io/oauth/oidc/login`, polling `localStorage` for the
`token`, but the harness does not need that flow because the API key authenticates the REST API
directly.

The validation handshake is expected to start with `GET /api/v1/auths/` for token or key validation.
Instance and feature checks use `GET /api/config`, which should return status, version, and feature
information. Available models are listed by `GET /api/models`; the vascular agent is exposed there as
a model or pipe identifier that still needs to be confirmed in T04.

Chat requests should use `POST /api/chat/completions`. The app uses streamed deltas over Socket.IO on
`/ws/socket.io`, including `chat:message:delta`, `chat:completion`, and source events, but the
harness should prefer `POST /api/chat/completions` with `stream: false` to obtain a synchronous full
response. Socket.IO should be treated only as a fallback if non-streaming responses are unsupported.
Retrieved passages or citations may appear in a `sources` field and should be confirmed during the T04
dry run.

All requests are made against `baseUrl = serverConfig.url`, with the bearer token injected per
request.
