# AI Options Trading Bot

## Credentials

Credentials must not be stored in this repository. Copy the variable names from
`credentials.env.example` into this external file and fill in the values there:

```text
/gdrive/MyDrive/trading/credentials.env
```

Restrict access to that file (for example, `chmod 600`) and never commit it. The
notebook uses the strict loader in `src/options_bot/credentials.py`; unsupported
names and malformed lines are rejected, and credential values are never logged.

> **Security notice:** Every credential that was previously committed must be
> revoked and rotated with its provider. Removing credentials from the current
> notebook does **not** remove them from Git history. Repository owners should
> separately rewrite history where appropriate and coordinate replacement clones.

The checked-in configuration starts safely with `DRY_RUN=True`,
`AUTO_MODE=False`, and `ENGINE_ENABLED=False`.
