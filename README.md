# AI Options Trading Bot

> **Safety:** This prototype is configured for paper trading by default. Trading
> can lose money, and profitability or passive income is not guaranteed.

## Where to keep credentials

Do not add credentials to this repository or notebook. In Google Drive, create:

```text
/gdrive/MyDrive/trading/credentials.env
```

Use [`credentials.env.example`](credentials.env.example) as the list of required
variable names. The file should look like this, with your own values after each
`=`:

```dotenv
ANGEL_API_KEY=your_value
ANGEL_CLIENT_CODE=your_value
ANGEL_PASSWORD=your_value
ANGEL_TOTP_SECRET=your_value
TELEGRAM_BOT_TOKEN=your_value
TELEGRAM_CHAT_ID=your_value
OPENAI_API_KEY=your_value
```

The notebook loads this file only after Google Drive is mounted. Keep the file
private and restrict access to the Drive account. `credentials.env` and common
database/data formats are ignored by Git as a second line of defense.

## Compromised credential warning

The original notebook and existing Git history contained credentials. Removing
them from the current file does **not** remove them from older commits. Revoke
and rotate the Angel One, Telegram, TOTP, and OpenAI credentials before using
this project. Rewrite and scan Git history before making the repository public.

