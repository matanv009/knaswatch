"""KnasWatch command line.

Identifying numbers are only ever read through getpass (hidden input) and handed
straight to the OS credential vault. They are never accepted as command line
arguments, because those are visible to any other process on the machine.
"""

import argparse
import getpass
import random
import secrets
import sys
import time

from . import INVOCATION, __version__
from .checker import (
    STATUS_CHALLENGE,
    STATUS_CLEAR,
    STATUS_ERROR,
    STATUS_FINES,
    check,
)
from .logging_setup import setup_logging
from .notify import (
    NotifyError,
    broadcast,
    format_result,
    pair_chat,
    send_message,
    send_to_chat,
)
from .validate import (
    is_plausible_license,
    is_valid_israeli_id,
    is_valid_profile_name,
    normalize_digits,
)
from . import schedule as scheduler
from . import state, vault

log = None  # set in main()

FAILURES_BEFORE_ALERT = 2

# How long to wait between people in the same run, in seconds. Several identity
# documents submitted from one machine seconds apart is a burst no household
# produces; a few minutes apart is simply two people who both drive. The gap is
# kept short when somebody is watching the window, because they are waiting for
# it - the unattended nightly run is the one that has to look unhurried.
GAP_UNATTENDED = (120.0, 420.0)
GAP_INTERACTIVE = (15.0, 45.0)


def _profile_gap_seconds(interactive: bool) -> float:
    low, high = GAP_INTERACTIVE if interactive else GAP_UNATTENDED
    return random.uniform(low, high)


def _prompt_secret(label: str, validator, error_message: str) -> str:
    while True:
        value = normalize_digits(getpass.getpass(f"{label} (hidden): "))
        if validator(value):
            return value
        print(f"  ! {error_message}")


def cmd_add_profile(args) -> int:
    name = args.name or input("Profile nickname (e.g. Dad, Mum, me): ").strip()
    if not is_valid_profile_name(name):
        print("Invalid nickname.")
        return 1

    print(f"\nEntering details for '{name}'. Typing is hidden and nothing is written to disk.")
    id_number = _prompt_secret(
        "  ID number (תעודת זהות)",
        is_valid_israeli_id,
        "That is not a valid Israeli ID number (check digit failed).",
    )
    licence = _prompt_secret(
        "  Driver's licence number",
        is_plausible_license,
        "Expected 6-9 digits.",
    )

    vault.save_credentials(name, id_number, licence)
    state.add_profile(name)
    print(f"\n  Stored in the OS credential vault ({vault.vault_backend_name()}).")
    print(f"  Profile '{name}' is ready.")
    return 0


def cmd_recipients(args) -> int:
    if vault.load_telegram() is None:
        print(f"Telegram is not configured. Run:  {INVOCATION} telegram")
        return 1

    recipients = vault.load_recipients()
    print("Alert recipients:")
    for recipient in recipients:
        print(f"  - {recipient.describe()}")
    print("\n'all profiles' means this person is told about everyone.")
    return 0


def cmd_add_recipient(args) -> int:
    config = vault.load_telegram()
    if config is None:
        print(f"Telegram is not configured. Run:  {INVOCATION} telegram")
        return 1

    profiles = state.list_profiles()
    for name in args.profile or []:
        if name not in profiles:
            print(f"No profile named '{name}'. Known profiles: {', '.join(profiles)}")
            return 1

    label = args.label or input("Name for this recipient (e.g. Mum): ").strip()
    if not label:
        print("A name is required.")
        return 1
    if any(r.label == label for r in vault.load_recipients()):
        print(f"A recipient called '{label}' already exists.")
        return 1

    print(f"\n{label} must first open Telegram and press Start on the bot.")
    print("Then have them send this code to the bot:")
    code = f"{secrets.randbelow(1_000_000):06d}"
    print(f"\n      {code}\n")
    print("Waiting up to 2 minutes for the code...")

    try:
        found = pair_chat(config.token, code)
    except NotifyError as exc:
        print(f"  ! {exc}")
        return 1
    if found is None:
        print("  ! The code did not arrive. Nothing was changed.")
        return 1
    if not found.is_private:
        print(f"  ! That code came from '{found.description}', which is a "
              f"{found.chat_type} chat.")
        print("    Every member would see the fine details, so it was not added. "
              "Ask them to send the code from a private chat with the bot.")
        return 1

    chat_id, description = found.chat_id, found.description
    recipients = vault.load_recipients()
    if any(r.chat_id == chat_id for r in recipients):
        print(f"  ! That Telegram account already receives alerts.")
        return 1

    recipients.append(
        vault.Recipient(chat_id=chat_id, label=label, profiles=tuple(args.profile or ()))
    )
    vault.save_recipients(recipients)
    print(f"  Linked to: {description}")

    try:
        send_to_chat(config.token, chat_id,
                     f"✅ <b>KnasWatch</b> - {label}, ההתראות יגיעו לכאן מעכשיו.")
    except NotifyError as exc:
        print(f"  ! Saved, but the test message failed: {exc}")
        return 1

    print(f"  Test message sent. {recipients[-1].describe()}")
    return 0


def cmd_test_notify(args) -> int:
    """Prove delivery works without visiting the government site.

    Useful because a check that finds nothing is silent by design, so there is
    otherwise no way to tell "notifications work" from "nothing to report".
    """
    config = vault.load_telegram()
    if config is None:
        print(f"Telegram is not configured. Run:  {INVOCATION} telegram")
        return 1

    text = "🔔 <b>KnasWatch</b> - בדיקת התראות. אם ההודעה הגיעה, הכול מחובר."

    # Deliberately not broadcast(): that skips recipients whose scope does not
    # match the profile being reported, and a connection test must reach
    # everyone. Reporting a skipped recipient as "sent" hid a real failure once.
    failures = 0
    for recipient in vault.load_recipients():
        try:
            send_to_chat(config.token, recipient.chat_id, text)
        except NotifyError as exc:
            failures += 1
            print(f"  ! failed for {recipient.label}: {exc}")
            continue
        print(f"  sent to {recipient.describe()}")

    return 1 if failures else 0


def cmd_remove_recipient(args) -> int:
    recipients = vault.load_recipients()
    if not recipients:
        print("Telegram is not configured.")
        return 1

    remaining = [r for r in recipients if args.name not in (r.label, r.chat_id)]
    if len(remaining) == len(recipients):
        print(f"No recipient called '{args.name}'. "
              f"Known: {', '.join(r.label for r in recipients)}")
        return 1
    if not remaining:
        print("That is the only recipient; removing it would leave nobody to alert.")
        print(f"Use '{INVOCATION} telegram' to reconfigure instead.")
        return 1

    vault.save_recipients(remaining)
    print(f"Removed '{args.name}'. Alerts now go to: "
          f"{', '.join(r.label for r in remaining)}")
    return 0


def _retarget_recipients(old: str, new=None) -> None:
    """Keep recipient routing in step with a renamed or deleted profile.

    On deletion, a recipient scoped only to that profile is dropped rather than
    left with an empty profile list - an empty list means "every profile", so
    leaving it would silently widen what that person is told about.
    """
    try:
        recipients = vault.load_recipients()
    except vault.VaultError:
        return
    if not recipients:
        return

    updated, dropped = [], []
    for recipient in recipients:
        if not recipient.profiles or old not in recipient.profiles:
            updated.append(recipient)
            continue
        profiles = tuple(p for p in recipient.profiles if p != old)
        if new:
            profiles += (new,)
        if profiles:
            updated.append(recipient._replace(profiles=profiles))
        else:
            dropped.append(recipient.label)

    if updated == recipients:
        return
    if not updated:
        print("  ! Every recipient was scoped only to that profile; "
              "leaving recipients untouched so alerts are not lost.")
        return

    vault.save_recipients(updated)
    for label in dropped:
        print(f"  Removed recipient '{label}' - they were only subscribed to '{old}'.")


def cmd_rename_profile(args) -> int:
    profiles = state.list_profiles()
    old = args.old or input("Current nickname: ").strip()
    if old not in profiles:
        print(f"No profile named '{old}'. Known profiles: {', '.join(profiles) or 'none'}")
        return 1

    new = args.new or input("New nickname: ").strip()
    if not is_valid_profile_name(new):
        print("Invalid nickname.")
        return 1
    if new == old:
        print("That is already the nickname.")
        return 0
    if new in profiles:
        print(f"A profile named '{new}' already exists.")
        return 1

    credentials = vault.load_credentials(old)
    if credentials is None:
        print(f"Profile '{old}' has no stored numbers; add it again instead.")
        return 1

    # Write the new vault entry before deleting the old one: if anything fails
    # in between, the numbers still exist under one name or the other.
    vault.save_credentials(new, credentials.id_number, credentials.license_number)
    state.rename_profile(old, new)
    vault.delete_credentials(old)
    _retarget_recipients(old, new)
    print(f"Renamed '{old}' to '{new}'. Stored numbers and history were kept.")
    return 0


def cmd_config(args) -> int:
    config = state.load_config()
    if args.toggle_all_clear:
        config["notify_all_clear"] = not config.get("notify_all_clear", False)
        state.save_config(config)
    elif args.all_clear is not None:
        config["notify_all_clear"] = args.all_clear == "on"
        state.save_config(config)

    on = config.get("notify_all_clear", False)
    print("Settings")
    print(f"  Daily 'all clear' message : {'on' if on else 'off'}")
    print("    on  - a message every day, so silence means something is broken")
    print("    off - messages only when fines appear or checks keep failing")
    return 0


def cmd_remove_profile(args) -> int:
    name = args.name
    if name not in state.list_profiles():
        print(f"No profile named '{name}'.")
        return 1
    vault.delete_credentials(name)
    state.remove_profile(name)
    _retarget_recipients(name)
    print(f"Profile '{name}' and its stored numbers were deleted.")
    return 0


def cmd_telegram(args) -> int:
    print("Telegram setup")
    print("  1. Open Telegram, message @BotFather, send /newbot and follow the prompts.")
    print("  2. Paste the token it gives you below.\n")

    token = getpass.getpass("Bot token (hidden): ").strip()
    if not token or ":" not in token:
        print("That does not look like a bot token.")
        return 1

    chat_id = args.chat_id
    if not chat_id:
        # Pairing code: bot usernames are public, so anyone can message the bot.
        # Linking to "whoever wrote last" would let a stranger who messaged at
        # the wrong moment silently become the recipient of every notification.
        # Only the chat that echoes this code back is accepted.
        code = f"{secrets.randbelow(1_000_000):06d}"
        print("\nTo link YOUR Telegram (and not someone who happened to message")
        print("the bot), send this code to your bot now, as a message:")
        print(f"\n      {code}\n")
        print("Waiting up to 2 minutes for the code...")
        try:
            found = pair_chat(token, code)
        except NotifyError as exc:
            print(f"  ! {exc}")
            return 1
        if found is None:
            print("  ! The code did not arrive. Run this again and send the code "
                  "from your phone.")
            return 1
        if not found.is_private:
            print(f"  ! That code came from '{found.description}', which is a "
                  f"{found.chat_type} chat.")
            print("    Everyone in it would see the fine details, so it was not "
                  "linked. Send the code from a private chat with the bot.")
            return 1
        chat_id, description = found.chat_id, found.description
        print(f"  Linked to: {description}")

    config = vault.TelegramConfig(token=token, chat_id=str(chat_id))
    try:
        send_message(config, "✅ <b>KnasWatch</b> connected successfully.")
    except NotifyError as exc:
        print(f"  ! Test message failed: {exc}")
        return 1

    # Keep anyone already receiving alerts. Saving without the recipient list
    # used to drop every extra recipient silently, because a missing list is
    # read back as "just the owner".
    existing = vault.load_recipients()
    recipients = [r for r in existing if r.chat_id != str(chat_id)]
    recipients.insert(0, vault.Recipient(chat_id=str(chat_id), label="me", profiles=()))

    vault.save_telegram(token, str(chat_id), recipients)
    print("  Test message sent and settings stored in the credential vault.")
    if len(recipients) > 1:
        others = ", ".join(r.label for r in recipients[1:])
        print(f"  Still sending alerts to: {others}")
    return 0


def cmd_setup(args) -> int:
    print(f"KnasWatch {__version__} - first time setup\n")
    print("Your ID and licence numbers are stored only in the operating system's")
    print("credential vault. They are never written to a file, so nothing sensitive")
    print("can end up in this folder or in a git commit.\n")

    if cmd_add_profile(argparse.Namespace(name=None)) != 0:
        return 1

    while input("\nAdd another person? [y/N]: ").strip().lower() == "y":
        if cmd_add_profile(argparse.Namespace(name=None)) != 0:
            return 1

    print()
    if input("Set up Telegram notifications now? [Y/n]: ").strip().lower() != "n":
        cmd_telegram(argparse.Namespace(chat_id=None))

    print()
    if input("Schedule the daily check now? [Y/n]: ").strip().lower() != "n":
        cmd_schedule(argparse.Namespace(at="09:00"))

    print(f"\nSetup complete. Run a check now with:  {INVOCATION} check --all")
    return 0


def _should_notify(profile: str, result, previous: dict, force: bool) -> bool:
    if force:
        return True

    if result.status == STATUS_FINES:
        return result.fingerprint() != previous.get("fingerprint")

    if result.status == STATUS_CLEAR:
        if previous.get("status") == STATUS_FINES:
            return True  # fines were paid or cancelled - worth knowing
        return state.load_config()["notify_all_clear"]

    if result.status == STATUS_CHALLENGE:
        # Only worth a message once: repeating it daily would be nagging.
        return previous.get("status") != STATUS_CHALLENGE

    # Errors: stay quiet about a one-off blip, speak up if it keeps failing.
    return previous.get("consecutive_failures", 0) + 1 >= FAILURES_BEFORE_ALERT


def cmd_check(args) -> int:
    profiles = state.list_profiles() if args.all else [args.profile]
    profiles = [p for p in profiles if p]

    if not profiles:
        print(f"No profiles configured. Run:  {INVOCATION} setup")
        return 1

    telegram = vault.load_telegram()
    recipients = vault.load_recipients()
    exit_code = 0
    visited = False  # whether this run has already been to the site
    # A scheduled run has nobody sitting in front of it, so it must not stop and
    # wait for a CAPTCHA to be answered.
    interactive = not args.unattended

    for profile in profiles:
        credentials = vault.load_credentials(profile)
        if credentials is None:
            log.error("Profile '%s' has no stored numbers; re-add it.", profile)
            exit_code = 1
            continue

        # Skip a profile that already succeeded recently. This is what lets the
        # day hold several scheduled attempts as CAPTCHA insurance while still
        # touching the site about once a day - and rare contact is precisely what
        # keeps reCAPTCHA from challenging us in the first place.
        if args.if_stale is not None:
            age = state.hours_since_success(profile)
            if age is not None and age < args.if_stale:
                log.info("Skipping %s - checked %.1fh ago.", profile, age)
                continue

        # Only after the skip above: a profile that was skipped never touched the
        # site, so there is nothing to space this one out from.
        if visited:
            gap = _profile_gap_seconds(interactive)
            log.info("Waiting %.0fs before the next person.", gap)
            time.sleep(gap)

        log.info("Checking %s...", profile)
        result = check(credentials, profile, headless=args.headless,
                       interactive=interactive)
        visited = True
        previous = state.get_profile_state(profile)

        if result.status in (STATUS_ERROR, STATUS_CHALLENGE):
            failures = previous.get("consecutive_failures", 0) + 1
            log.error("%s: %s (%s)", profile, result.summary, result.detail)
            exit_code = 1
        else:
            failures = 0
            log.info("%s: %s", profile, result.summary)

        should_notify = _should_notify(profile, result, previous, args.force_notify)

        # Notify BEFORE recording the fingerprint. The fingerprint is what marks
        # a result as already reported, so writing it first would mean a failed
        # send is never retried: the next run would see an unchanged fingerprint
        # and stay silent about fines the user was never told about.
        notified = True
        if should_notify:
            if telegram is None:
                log.warning("Telegram is not configured; skipping notification.")
            else:
                wanted = [r for r in recipients if r.wants(profile)]
                if not wanted:
                    log.warning("No recipient is subscribed to %s.", profile)
                else:
                    failed = broadcast(
                        telegram.token, recipients, profile, format_result(profile, result)
                    )
                    delivered = len(wanted) - len(failed)
                    log.info("Notification for %s sent to %d of %d recipient(s).",
                             profile, delivered, len(wanted))
                    if failed:
                        exit_code = 1
                    # Only a total failure blocks the fingerprint; if at least
                    # one person heard, the result counts as reported.
                    notified = delivered > 0

        if not notified:
            log.warning("Keeping the previous fingerprint for %s so the alert is "
                        "retried on the next run.", profile)

        state.update_profile_state(
            profile,
            status=result.status,
            fingerprint=(result.fingerprint() if result.ok and notified
                         else previous.get("fingerprint")),
            summary=result.summary,
            failures=failures,
            success=result.ok,
        )

    return exit_code


def cmd_status(args) -> int:
    profiles = state.list_profiles()
    print(f"KnasWatch {__version__}")
    print(f"  Credential vault : {vault.vault_backend_name()}")
    print(f"  Data folder      : {state.DATA_DIR}")
    print(f"  Telegram         : {'configured' if vault.load_telegram() else 'not configured'}")
    if sys.platform == "win32":
        print(f"  Scheduled task   : {scheduler.task_status()}")

    if not profiles:
        print(f"\n  No profiles yet. Run:  {INVOCATION} setup")
        return 0

    print("\n  Profiles:")
    saved = state.load_state()
    for profile in profiles:
        info = saved.get(profile, {})
        checked = info.get("checked_at", "never")
        summary = info.get("summary", "-")
        print(f"    {profile:<20} {checked:<28} {summary}")
    return 0


def cmd_schedule(args) -> int:
    try:
        name = scheduler.create_task(args.at)
    except RuntimeError as exc:
        print(f"  ! {exc}")
        return 1
    print(f"  Scheduled task '{name}' created; it runs daily at {args.at}.")
    print("  If the PC is off at that time, it runs at the next login instead.")
    return 0


def cmd_unschedule(args) -> int:
    scheduler.delete_task()
    print("  Scheduled task removed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knaswatch",
        description="Daily check for Israeli traffic fines (ecom.gov.il).",
    )
    parser.add_argument("--verbose", action="store_true", help="verbose logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="first time setup wizard").set_defaults(func=cmd_setup)

    p = sub.add_parser("check", help="run a check now")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="check every profile")
    group.add_argument("--profile", help="check one profile by nickname")
    p.add_argument("--headless", action="store_true",
                   help="hide the browser window (the site almost always answers "
                        "a hidden browser with a CAPTCHA, so this rarely works)")
    p.add_argument("--unattended", action="store_true",
                   help="never wait for a CAPTCHA to be answered; used by the scheduled task")
    p.add_argument("--force-notify", action="store_true",
                   help="send a notification even if nothing changed")
    p.add_argument("--if-stale", type=float, metavar="HOURS", default=None,
                   help="skip a profile that already succeeded within HOURS; lets the "
                        "day hold spare attempts without extra visits to the site")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("add-profile", help="add a person")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_add_profile)

    sub.add_parser("recipients", help="list who receives alerts").set_defaults(
        func=cmd_recipients)

    p = sub.add_parser("add-recipient", help="send alerts to another person's Telegram")
    p.add_argument("--label", help="name for this recipient")
    p.add_argument("--profile", action="append",
                   help="only alert them about this profile (repeatable; "
                        "omit for every profile)")
    p.set_defaults(func=cmd_add_recipient)

    sub.add_parser("test-notify", help="send a test message to every recipient").set_defaults(
        func=cmd_test_notify)

    p = sub.add_parser("remove-recipient", help="stop sending alerts to someone")
    p.add_argument("name", help="their label or chat id")
    p.set_defaults(func=cmd_remove_recipient)

    p = sub.add_parser("rename-profile", help="change a person's nickname")
    p.add_argument("old", nargs="?")
    p.add_argument("new", nargs="?")
    p.set_defaults(func=cmd_rename_profile)

    p = sub.add_parser("config", help="show or change settings")
    p.add_argument("--all-clear", choices=["on", "off"],
                   help="send a message every day even when nothing was found")
    p.add_argument("--toggle-all-clear", action="store_true",
                   help="flip the daily all-clear message on or off")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("remove-profile", help="delete a person and their stored numbers")
    p.add_argument("name")
    p.set_defaults(func=cmd_remove_profile)

    p = sub.add_parser("telegram", help="configure Telegram notifications")
    p.add_argument("--chat-id", help="set the chat id manually")
    p.set_defaults(func=cmd_telegram)

    sub.add_parser("status", help="show configuration and last results").set_defaults(func=cmd_status)

    p = sub.add_parser("schedule", help="create the daily scheduled task (Windows)")
    p.add_argument("--at", default="09:00", help="time of day, HH:MM (default 09:00)")
    p.set_defaults(func=cmd_schedule)

    sub.add_parser("unschedule", help="remove the scheduled task").set_defaults(func=cmd_unschedule)

    return parser


def main(argv=None) -> int:
    global log
    args = build_parser().parse_args(argv)
    log = setup_logging(verbose=args.verbose)

    try:
        return args.func(args)
    except vault.VaultError as exc:
        print(f"  ! {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
