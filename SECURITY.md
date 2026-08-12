# Security model

KnasWatch handles two numbers that identify a person completely: an Israeli ID
number (תעודת זהות) and a driver's licence number. Everything below exists to
keep them where they belong.

## Where the numbers live

**In the operating system's credential vault, and nowhere else.**

| Platform | Backend |
|---|---|
| Windows | Credential Manager (`WinVaultKeyring`) |
| macOS | Keychain |
| Linux | Secret Service (GNOME Keyring / KWallet) |

Access goes through the [`keyring`](https://pypi.org/project/keyring/) library,
under the service name `KnasWatch`, one entry per profile. `python -m knaswatch status`
prints which backend is in use, so you can confirm rather than trust.

**No KnasWatch code path writes an identifying number to a file.** Not to config,
not to state, not to the log, not to a cache. If you delete a profile, the vault
entry is deleted with it.

## Why the repository is safe to publish

Because there are no secrets in it to leak. This is a structural property, not a
`.gitignore` rule: the numbers are in the OS vault, and the settings that *are*
files live in `%LOCALAPPDATA%\KnasWatch` — outside the repository folder
entirely. You can fork, share, or accept pull requests without ever risking a
credential in a commit. (`.gitignore` still covers logs and browser profiles, as
a second line of defence against a redirected output file.)

## How the numbers are entered

Through `getpass`, hidden as you type.

They are deliberately **not** accepted as command line arguments and **not** read
from environment variables. Command lines are visible to any other process on the
machine via the process table, and both tend to end up in shell history, crash
reports, and CI logs.

## What leaves your machine

| Destination | Contents |
|---|---|
| `ecom.gov.il` | The ID and licence number — the lookup itself. Over HTTPS. |
| `api.telegram.org` | Profile nickname, fine descriptions and amounts. **Never the numbers.** |
| Anywhere else | Nothing. |

Notification text is built in `notify.format_result`, which receives only the
nickname and the result object. The identifying numbers are not in scope there,
so they cannot be interpolated in by mistake.

## Log redaction

Logs are written to `%LOCALAPPDATA%\KnasWatch\knaswatch.log`. No call site logs
an identifying number, and a filter on the log handler masks any run of seven or
more digits as a backstop. Fine amounts are three or four digits, so ordinary
output is unaffected.

This is a safety net for future changes, not the primary defence.

## Multiple people

Each profile is a separate vault entry. Someone else's numbers are entered on
**their** computer, into **their** vault — this project has no shared
installation, no server and no account system, so there is no mechanism by which
one person's details could reach another person's machine.

## The Telegram bot

**Anyone on Telegram can message any bot.** Usernames are public and this cannot
be switched off, so KnasWatch is built so that incoming messages are worthless:

- **The bot only sends.** Outside of setup, no KnasWatch code reads incoming
  messages — there is no polling loop, no command handler, no reply logic. A
  stranger who messages the bot gets silence, and nothing they write is ever
  read, stored, or acted upon.
- **Linking requires proof, not timing.** During setup a random pairing code is
  shown on your screen, and only the chat that sends back exactly that code is
  linked. Without this, "link to whoever messaged the bot last" would let a
  stranger who messaged at the wrong moment silently become the recipient of
  every notification.
- **Notifications go to a fixed list of chat ids**, stored in the OS vault next
  to the token. Messaging the bot cannot add anyone: every recipient is added at
  the keyboard, and each one must echo back a fresh pairing code before they are
  saved.
- **Each recipient can be scoped to particular profiles**, so one household can
  route each person's fines to that person rather than sharing everyone's with
  everyone. A recipient with no scope receives every profile. Renaming a profile
  updates the routing; deleting one removes any recipient left with an empty
  scope, because an empty scope means "everything" and silently widening what
  somebody is told about would be a privacy failure.
- **The token is treated as a secret**: entered hidden, stored in the vault, and
  scrubbed from error messages (Bot API URLs embed the token, and HTTP error
  text embeds the URL — so error text is redacted before it can reach a log).

What the bot token *would* give an attacker if it leaked: the ability to send
you fake messages and to read messages people send to the bot. It does not give
access to the ID or licence numbers, which never reach Telegram in any form. If
the token ever leaks, revoke it with @BotFather (`/revoke`) and run setup again.

## CAPTCHA and bot protection

The site uses invisible reCAPTCHA. KnasWatch does not try to get around it:

- No fingerprint spoofing, no patched `navigator.webdriver`, no stripping of
  Chrome's own automation flags.
- No CAPTCHA-solving service, paid or otherwise.
- When a challenge appears, the run stops and asks the person to answer it.

If you fork this project, please keep it that way. Anti-detection measures would
turn a personal convenience tool into something the site operator is entitled to
treat as an attack.

## What this does *not* protect against

Stated plainly, because a security document that only lists strengths is not
useful:

1. **Anyone with access to your logged-in operating system account can read the
   vault.** This is the same trust boundary your browser uses for saved
   passwords. Protecting against it would mean a master password on every run,
   which would make unattended daily checks impossible. Lock your screen.
2. **Malware running as you** can read the vault, the same as it could read your
   browser's passwords or key your input.
3. **Anyone with physical access to an unlocked machine** can run
   `python -m knaswatch check` and see the results — though not the stored
   numbers, which are never displayed back.
4. **The site itself.** Your query reaches a government server, as it would if
   you filled the form by hand.

## Dependencies

Four, all pinned in `requirements.txt`: `playwright`, `keyring`, `platformdirs`,
`httpx`. The dependency surface is kept small on purpose — every added package is
code with access to a process that handles identifying numbers.

## Reporting a vulnerability

**Please report security problems privately, not as a public issue.** Use
GitHub's private vulnerability reporting: the **Security** tab of this
repository → **Report a vulnerability**. Only the maintainers can see it.

A public issue describing a flaw is readable by everyone the moment it is
posted, including before there is a fix — and this tool handles identity
documents, so that window matters. Ordinary bugs, questions and feature ideas
are very welcome as normal issues; it is only weaknesses that should start
privately.

Whatever the channel, **never include a real ID number, driver's licence number,
Telegram bot token, or chat id** in a report, a screenshot, or a log excerpt.
The log at `%LOCALAPPDATA%\KnasWatch\knaswatch.log` masks runs of seven or more
digits, but do check an excerpt before pasting it.

Expect an acknowledgement within a few days. This is a personal project with no
bounty programme and no formal response window; it is maintained on a
best-effort basis, and that is worth knowing before you rely on it.
