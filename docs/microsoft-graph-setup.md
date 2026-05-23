# Microsoft Graph setup

The Calendar collector (active since v0.1.1) and the Teams collector (currently a stub) talk to Microsoft Graph using the **device-code flow** — no client secret, no redirect URL, ideal for a desktop agent. This guide is written for someone who has never registered an Azure app before.

> Once configured, the refresh token caches to `~/.config/dashd/msgraph_token.json` (mode 0600). The first run prints a device-code URL; sign in once and the agent re-uses the cached token across restarts.

## 1. Register an app in Microsoft Entra ID (Azure AD)

1. Sign in to <https://entra.microsoft.com> with the Microsoft account whose calendar/Teams you want to read.
2. **Identity → Applications → App registrations → + New registration**.
3. Name: `dashd` (anything, just for your reference).
4. Supported account types: **Accounts in this organizational directory only** (single tenant) is fine for a personal/work tenant. Pick "Personal Microsoft accounts" only if you're connecting an `@outlook.com` / `@hotmail.com` account.
5. Redirect URI: leave blank.
6. Click **Register**.
7. From the overview page, copy:
   - **Application (client) ID** → goes into `config.toml` as `collectors.calendar.client_id`
   - **Directory (tenant) ID** → `collectors.calendar.tenant_id` (or use `"common"` for personal accounts)

## 2. Enable public-client (device-code) flow

1. **Authentication → Advanced settings → Allow public client flows → Yes → Save**.

## 3. Grant delegated scopes

1. **API permissions → + Add a permission → Microsoft Graph → Delegated permissions**.
2. Add:
   - `Chat.Read`        — Teams chat unread count
   - `Calendars.Read`   — calendar events
   - `Mail.Read`        — only if you want Graph email instead of IMAP
   - `User.Read`        — included by default
3. Click **Grant admin consent** (admin tenants) or accept on first sign-in (personal accounts).

## 4. Wire it into dashd

`~/.config/dashd/config.toml`:

```toml
[collectors.calendar]
enabled = true
provider = "microsoft"
client_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
tenant_id = "common"   # or your tenant GUID

[collectors.teams]
enabled = true
# Reuses calendar client_id/tenant_id
```

## 5. First-run device code

Run the agent once with the calendar collector enabled. It prints something like:

```
To sign in, use a web browser to open https://microsoft.com/devicelogin and enter the code ABCD-1234
```

Sign in once. The refresh token is cached via the OS keyring if `keyring` is installed, otherwise to `~/.config/dashd/msgraph_token.json` with `0600` permissions. Subsequent runs are silent.

## Troubleshooting

- **`AADSTS50020` invalid account type** — your tenant config says "single tenant" but you're signing in with a personal Microsoft account. Either change the registration to multi-tenant + personal, or use a work account.
- **`AADSTS65001` consent required** — open the registration's "API permissions" page and click "Grant admin consent".
- **Token expired and no refresh** — delete `~/.config/dashd/msgraph_token.json` (or clear the keyring entry) and re-run.
