# Repository guidance

- Support Python 3.10+ and the existing PythonAnywhere Flask/WSGI deployment. Do not
  replace `PicklePersistence`, Telegram handlers, or stored user-data keys without a
  backward-compatible migration.
- Treat `Habitica_API.py` as the Python application's Habitica HTTP boundary. Use its
  bounded requests and sentinel contracts; do not add direct requests to handlers.
- Never commit or log tokens, Habitica credentials, raw Telegram updates, pickle data,
  private avatar payloads, or host paths. Use environment variables documented in the
  README and keep `BOT_DATA_PATH` outside the repository on production hosts.
- Webhook and tick persistence transactions must retain the cross-process runtime lock.
  Do not run polling and WSGI against the same pickle file concurrently.
- `habitica-avatar.bundle.js` is a tracked generated asset needed at runtime. Do not edit
  it manually; rebuild it with `npm ci && npm run build:avatar` and review its diff.
- Preserve unrelated working-tree changes. Add focused tests for behavior changes, then
  run `python -m pytest`, `python -m ruff check .`, and `npm test` when available.
- `habitica_bot.py` retains its legacy CRLF line endings via `.gitattributes`; avoid
  repository-wide formatting or line-ending churn in that large module.
