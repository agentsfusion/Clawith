# Lark / 飞书 CLI Integration Setup Guide

This guide explains how to configure the Lark/Feishu CLI integration in Clawith.

## Prerequisites

- A Lark/Feishu account with admin access to the [Lark Developer Console](https://open.larksuite.com/app) (or [Feishu Developer Console](https://open.feishu.cn/app) for China)
- Clawith deployed with the Lark CLI integration enabled

## Step 1: Create a Lark App

1. Go to the [Lark Developer Console](https://open.larksuite.com/app) (international) or [Feishu Developer Console](https://open.feishu.cn/app) (China)
2. Click **Create Custom App**
3. Fill in the app name and description
4. Note down the **App ID** and **App Secret** from the app's credentials page

## Step 2: Configure OAuth Permissions

1. In the app settings, go to **Permissions & Scopes**
2. Enable the permissions your agents will need. Common ones:
   - `contact:user.base:readonly` — Read user profiles
   - `calendar:calendar` — Calendar access
   - `drive:drive` — Drive file access
   - `docx:document` — Document access
   - `sheets:spreadsheet` — Sheets access
   - `im:message` — Read messages
   - `im:message:send_as_bot` — Send messages
   - `mail:mail` — Mail access
   - `task:task` — Tasks access
   - `wiki:wiki` — Wiki access
3. Go to **Security Settings** → **Redirect URL** and add:
   - Production: `https://your-domain.com/api/lark/auth/callback`
   - Local development: `http://localhost:8008/api/lark/auth/callback`

## Step 3: Publish the App

1. Go to **Version Management** → **Create Version**
2. Submit for review (internal apps are approved immediately)
3. Set the app to be available to all users in your organization

## Step 4: Configure Clawith Enterprise Settings

1. Log in to Clawith as an org admin
2. Go to **Enterprise Settings**
3. Find the **Lark / 飞书** configuration card
4. Enter:
   - **App ID**: From Step 1
   - **App Secret**: From Step 1
   - **Brand**: Select `Lark` (international) or `Feishu` (China)
   - **Scope Preset**: Choose a permission preset or select custom scopes
5. Click **Save**
6. Click **Import Lark Skills** to import the 20 Lark agent skills

## Step 5: Connect User Accounts

Each user who wants their agent to access Lark must connect their own account:

1. Go to the agent's settings page
2. Find the **Lark / 飞书** section
3. Click **Connect Lark Account**
4. Complete the OAuth flow in the popup window
5. After successful authorization, the connected account appears in the list

## Brand Selection: Lark vs Feishu

| Brand | Endpoint | Region |
|-------|----------|--------|
| Lark | `open.larksuite.com` | International |
| Feishu | `open.feishu.cn` | China |

Choose the brand that matches your Lark/Feishu organization. This determines which OAuth and API endpoints are used.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LARK_OAUTH_CALLBACK_PATH` | `/api/lark/auth/callback` | OAuth callback path |
| `LARK_OAUTH_REDIRECT_URI` | _(auto-generated)_ | Override OAuth redirect URI (for local development only) |

For local development with the Desktop OAuth flow, set:
```
LARK_OAUTH_REDIRECT_URI=http://localhost:8008/api/lark/auth/callback
```

## Troubleshooting

### "Lark not configured for tenant"
The org admin has not configured App ID/Secret in Enterprise Settings. Contact your admin.

### "Failed to connect Lark" during OAuth
- Verify the App ID and App Secret are correct
- Check that the Redirect URL matches in the Lark Developer Console
- Ensure the app is published and approved
- Check that the selected scopes match the app's enabled permissions

### "Lark CLI is not available"
The `lark-cli` binary will be auto-installed via `npm install -g @larksuite/cli`. If auto-installation fails:
- Ensure Node.js ≥ 16 is installed on the server
- Manually install: `npm install -g @larksuite/cli`
- Verify: `lark-cli --version`

### Token refresh failures
Lark user access tokens expire periodically. If refresh fails, the user needs to re-authorize their account. Check that the App Secret is still valid.

### Permission errors during command execution
If a `lark-cli` command returns a permission error:
- The user's OAuth token may not have the required scope
- The Lark app may not have the corresponding permission enabled
- Try re-authorizing with a broader scope preset
