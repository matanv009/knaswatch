# KnasWatch · קנס-ווטש

A daily checker for Israeli traffic fines. It queries the Enforcement and
Collection Authority portal ([ecom.gov.il](https://ecom.gov.il/voucherspa/input/318))
with your ID number and driver's licence number, and sends you a Telegram message
when something new appears.

Runs entirely on your own computer. There is no server, no account, and no
KnasWatch service — everyone who uses it runs their own copy.

---

## Read this first: how automatic it really is

The government site protects its lookup form with **invisible reCAPTCHA**. That
has real consequences, measured against the live site rather than assumed:

| Setup | Result |
|---|---|
| Plain HTTP requests, no browser | **Never works.** The submit endpoint requires a reCAPTCHA token that only a browser can produce. |
| Hidden (headless) browser | **Always challenged.** Every attempt was served an image puzzle. |
| Normal visible Chrome window | **Usually passes silently**, but not always — repeated checks in a short period get challenged. |
| Cloud / VPS / GitHub Actions | **Expect challenges.** Datacenter IP addresses score badly. |

So KnasWatch opens a real Chrome window for a few seconds each day. Most of the
time the check completes on its own and you only see the Telegram message. When
the site does ask for a CAPTCHA, KnasWatch **stops and asks you to answer it** —
it makes no attempt to detect-proof itself, disguise the browser, or use a
CAPTCHA-solving service. Defeating a site's bot protection is not something this
project does.

If you want notifications with no involvement at all, register for the official
personal area (אזור אישי) at [mgk.eca.gov.il](https://mgk.eca.gov.il/), which can
send alerts about new debts directly. KnasWatch is for people who would rather
poll the public form themselves than hand over another registration.

---

## Security

Your ID and licence numbers are the whole point of the design.

- **They are stored only in the operating system's credential vault** — Windows
  Credential Manager, macOS Keychain, or Linux Secret Service. They are never
  written to a file. That is why this repository is safe to fork and share:
  there is nothing sensitive in it to leak.
- **You type them once, hidden**, like a password. They are never accepted as
  command line arguments, because those are visible to other processes.
- **Notifications and logs never contain them.** Telegram messages carry only the
  profile nickname and the result. The log formatter additionally masks any run
  of 7 or more digits, so a careless future log line cannot leak one either.
- **Settings live outside this folder**, in `%LOCALAPPDATA%\KnasWatch`, so nothing
  can be committed by accident.

Full threat model, including what this does *not* protect against: [SECURITY.md](SECURITY.md).

---

## Install

Requires Python 3.10+ and Google Chrome.

```bash
git clone <your-fork-url> knaswatch
cd knaswatch
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

Calling `.venv\Scripts\python.exe` directly avoids activation entirely, which
saves a common mistake: a plain `python` picks up your system installation,
where the dependencies are not installed, and fails with
`ModuleNotFoundError: No module named 'playwright'`.

## Set up

On Windows, **double-click `knaswatch.bat`** in Explorer. It opens a menu — choose
option 1 for first-time setup. The window stays open so you can read the results.

Or from a terminal, where the launcher always picks the right Python regardless of
which directory you are in:

```bash
knaswatch.bat setup
```

On macOS and Linux:

```bash
.venv/bin/python -m knaswatch setup
```

The wizard walks through three things:

1. **Profiles.** One per person — you, a partner, a parent. Each has a nickname
   and their own ID and licence number. The nickname is all that ever appears in
   notifications.
2. **Telegram.** Message [@BotFather](https://t.me/BotFather), send `/newbot`,
   and paste the token. KnasWatch then shows a pairing code on screen — send it
   to your bot from your phone, and only the chat that sends that exact code is
   linked (so a stranger who happens to message your bot can never become the
   recipient). A test message confirms the link.
3. **Schedule.** Creates a daily Windows task.

## Use

```bash
knaswatch.bat telegram            # connect Telegram notifications
knaswatch.bat recipients          # who currently gets the alerts
knaswatch.bat add-recipient       # also alert someone else's Telegram
knaswatch.bat check --all         # check everyone now
knaswatch.bat check --profile אבא  # check one person
knaswatch.bat status              # configuration and last results
knaswatch.bat add-profile         # add another person
knaswatch.bat remove-profile אבא   # delete a person and their stored numbers
knaswatch.bat unschedule          # remove the daily task
```

## Scheduling

### Does the computer need to be on 24/7?

**No.** The scheduled task is created with "start as soon as possible after a
missed start", so a check missed while the machine was off simply runs the next
time you log in. A daily check has no need for precise timing, and three
triggers a day give it several chances to catch you logged in.

What it does need is to be **logged in** at some point during the day — not just
powered on. The task runs as you (which is why no Windows password is ever
stored), and the check drives a visible browser window, which needs an
interactive session. If the PC stays off for a week, you get one check when you
next log in, not seven.

There is no server involved, and nothing runs while your machine is off. If you
want a genuinely daily check without thinking about it, any always-on home
machine works — a mini PC or a Raspberry Pi on your home network is ideal,
because a residential IP is also what keeps the CAPTCHA quiet (see below).

### About the CAPTCHA

The site is protected by invisible reCAPTCHA. It is *invisible* by design: a
normal-looking session passes with no interaction at all, and an image challenge
only appears when the session looks automated. KnasWatch does not solve
challenges — it avoids provoking them:

- **A visible browser window.** Measured against the live site, a hidden
  (headless) browser is challenged far more often. `--headless` exists but is not
  the default for this reason.
- **Real Chrome with a persistent profile**, kept in the data folder, so the
  session looks like a returning user rather than a fresh robot every time.
- **Roughly one visit per day per person**, at a time that varies by up to 20
  minutes. Frequency is the single biggest factor: several submissions in a few
  minutes will get you challenged, a daily check generally will not.
- **Three scheduled attempts a day, not three visits.** The later runs use
  `--if-stale 20` and exit immediately once the day's check has succeeded. They
  exist only so that an occasional challenge costs a few hours instead of the
  whole day.

If a challenge does appear while you are at the machine, answer it once in the
window; that also improves how the profile is scored afterwards. Unattended runs
never sit waiting for a click.

`knaswatch.bat schedule --at 09:00` creates a Windows scheduled task with two
deliberate settings:

- **Run only when logged on**, so no Windows account password is ever stored.
- **Start as soon as possible after a missed start**, so **the computer does not
  need to be on 24/7**. If it was off at 09:00, the check runs at the next login.

The scheduled run passes `--unattended`: with nobody sitting there, a CAPTCHA
challenge is reported by Telegram rather than waited on.

On macOS or Linux, add a cron entry instead:

```bash
0 9 * * * cd /path/to/knaswatch && .venv/bin/python -m knaswatch check --all --unattended
```

## Notifications

You are messaged when something changes, not every day:

- **New or changed fines** — the list and the total.
- **Fines cleared** — when a previously reported debt is gone.
- **Repeated failures** — after two consecutive failed checks.
- **A CAPTCHA challenge** — once, asking you to run the check yourself.

A daily "all clear" is off by default. Turn it on by setting
`"notify_all_clear": true` in `%LOCALAPPDATA%\KnasWatch\config.json`.

## Being a good neighbour

One request per person per day, retried at most twice with a delay. This is a
personal query against a public form, at a volume far below normal human use.
Please do not raise the frequency.

## Licence

MIT. Provided as-is; always confirm anything important against the official site.
