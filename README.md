# kaufland-cli

Unofficial CLI for Kaufland Card digital receipts. It exports your purchase history as JSON or CSV: item names, quantities, prices and discounts, with no screenshots of the app.

## Install

```sh
uvx --from git+https://github.com/shinzo13/kaufland-cli kaufland --help
```

Or clone and run `uv run kaufland --help`.

## Use

```sh
kaufland login --email you@example.com   # once; password is prompted
kaufland list -n 10                      # one row per receipt
kaufland items -n 10 --output csv        # one row per purchased item
kaufland receipts --all                  # raw API payloads, full history
kaufland status
kaufland logout
```

## Config

| Variable | Default | Purpose |
|---|---|---|
| `KAUFLAND_COUNTRY` | `PL` | Two-letter country of your Kaufland account |
| `KAUFLAND_UI_LOCALES` | derived from country | Login page locale |
| `KAUFLAND_DATA_DIR` | `$XDG_CONFIG_HOME/kaufland-cli` | Token storage |
| `KAUFLAND_EMAIL` / `KAUFLAND_PASSWORD` | unset | Non-interactive login |
| `KAUFLAND_TIMEOUT_SECONDS` | `30` | HTTP timeout |

## Notes

- Auth is OAuth2 with PKCE against cidaas (`account.kaufland.com`) using the mobile client id. Receipts come from `p.crm-dynamics.schwarz/api/v2/customers/<sub>/transactions`.
- Login runs without a browser. The client accepts only the `com.kaufland.kaufland://` redirect, which no browser can follow, so credentials are posted straight to the cidaas login route.
- Tokens are stored in `tokens.json` with mode `0600` and refresh themselves, so `login` is needed only once. Your password is sent only to `account.kaufland.com`.
- Amounts arrive in minor units. `items` and `list` convert them, `receipts` does not.
- This is an app-facing API that can change without notice. Go easy on `--all`. Only receipts where the card was scanned at checkout exist in the history.
