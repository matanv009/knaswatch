"""Drives the ecom.gov.il form with a real browser and reads the JSON it returns.

Why a browser and not plain HTTP calls: the submit endpoint
(PostIdentificationData) only accepts a request carrying a reCAPTCHA token, and
that token can only be produced by executing Google's script in a browser.

Why the window is visible: measured against the live site, a headless browser is
always served an image challenge, while a normal visible Chrome window is often
let through silently. KnasWatch deliberately does nothing to disguise itself as
a human - no automation flags are stripped, no fingerprint is spoofed. When the
site does challenge, the tool stops and asks the person to answer it themselves.
"""

import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from . import INVOCATION
from .state import DATA_DIR
from .vault import Credentials

log = logging.getLogger("knaswatch")

URL = "https://ecom.gov.il/voucherspa/input/318"

# Identification type 10 = "תעודת זהות ומספר רישיון נהיגה", whose related fields
# are k_id_tz (ID number) and k_id_num (driver's licence number).
SERVICE_VALUE = "10"
FIELD_ID = "#k_id_tz"
FIELD_LICENCE = "#k_id_num"

SUBMIT_ENDPOINT = "PostIdentificationData"
BASKET_ENDPOINTS = ("GetBasket", "GetBasketSummary")

# Every profile shared this one directory before browser profiles were split per
# person. It is migrated on first use rather than left behind; see
# _claim_legacy_profile.
LEGACY_PROFILE_DIR = DATA_DIR / "browser-profile"

NAV_TIMEOUT_MS = 60_000
SUBMIT_WAIT_MS = 25_000
SOLVE_WAIT_MS = 180_000  # how long to leave a challenge on screen for the user

STATUS_CLEAR = "clear"
STATUS_FINES = "fines"
STATUS_ERROR = "error"
STATUS_CHALLENGE = "challenge"

# Verified against the live site: a clean record and a wrong ID/licence pair are
# reported with *different* wording, so the two can be told apart safely.
#   clean    -> "חייב לא זוהה במערכת / לזיהוי זה אין תיקים פתוחים"
#   mismatch -> "אין התאמה בין מס. זהות ורישיון נהיגה או תיק מרכז"
_NO_DEBT_PHRASES = (
    "לא נמצא", "אין חוב", "לא קיימים חובות", "לא קיים חוב",
    "אין תיקים פתוחים", "לא זוהה במערכת",
)

# Checked before the no-debt phrases: if a reply ever contained both, "no match"
# is the conservative reading, and must never be shown as an all-clear.
_MISMATCH_PHRASES = ("אין התאמה",)

_AMOUNT_KEYS = ("sum", "amount", "debtamount", "totalsum", "payamount", "price", "total")
_LABEL_KEYS = (
    "description", "name", "text", "title", "reportnumber", "casenumber",
    "vouchername", "debttype", "reportnum", "caseid",
)


@dataclass
class CheckResult:
    status: str
    summary: str
    fines: list[dict] = field(default_factory=list)
    total_amount: Optional[float] = None
    detail: str = ""
    # Whether trying again could plausibly give a different answer. Set False
    # for outcomes a retry cannot fix - wrong ID/licence, or a CAPTCHA challenge.
    # This is decided where the outcome is classified rather than inferred later
    # from the wording of `summary`, which would break the moment a message is
    # reworded or translated.
    retryable: bool = True

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_CLEAR, STATUS_FINES)

    def fingerprint(self) -> str:
        """Stable signature of the outcome, so the same fines are not reported
        again every single day."""
        if self.status == STATUS_CLEAR:
            return "clear"
        parts = sorted(f"{f.get('label', '')}|{f.get('amount', '')}" for f in self.fines)
        return "fines:" + ";".join(parts)


def _looks_like_no_debt(messages: list[str]) -> bool:
    joined = " ".join(messages)
    if any(phrase in joined for phrase in _MISMATCH_PHRASES):
        return False
    return any(phrase in joined for phrase in _NO_DEBT_PHRASES)


def _looks_like_mismatch(messages: list[str]) -> bool:
    joined = " ".join(messages)
    return any(phrase in joined for phrase in _MISMATCH_PHRASES)


def _walk(node: Any):
    """Yield every dict nested anywhere inside a decoded JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _as_amount(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("₪", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _extract_fines(payload: Any) -> list[dict]:
    """Pull fine-like records out of the basket response.

    The basket schema is not published, so this looks for any nested object
    carrying a monetary value rather than assuming fixed key names.
    """
    fines: list[dict] = []
    seen: set[tuple] = set()

    for node in _walk(payload):
        amount = None
        for key, value in node.items():
            if key.lower() in _AMOUNT_KEYS:
                candidate = _as_amount(value)
                if candidate is not None and candidate > 0:
                    amount = candidate
                    break
        if amount is None:
            continue

        label = ""
        for key, value in node.items():
            if key.lower() in _LABEL_KEYS and isinstance(value, str) and value.strip():
                label = value.strip()
                break

        signature = (label, amount)
        if signature in seen:
            continue
        seen.add(signature)
        fines.append({"label": label or "חוב", "amount": amount})

    return fines


class _ResponseCollector:
    """Captures the API responses we care about as the page makes them."""

    def __init__(self) -> None:
        self.submit: Optional[dict] = None
        self.basket: list[dict] = []
        self.http_errors: list[str] = []

    def handle(self, response) -> None:
        url = response.url
        if SUBMIT_ENDPOINT not in url and not any(e in url for e in BASKET_ENDPOINTS):
            return
        try:
            if response.status >= 400:
                self.http_errors.append(f"{response.status} on {url.rsplit('/', 1)[-1]}")
                return
            body = response.json()
        except Exception:
            return

        if SUBMIT_ENDPOINT in url:
            self.submit = body
        else:
            self.basket.append(body)


def _classify(collector: _ResponseCollector) -> CheckResult:
    submit = collector.submit

    if submit is None:
        detail = "; ".join(collector.http_errors) or "no response from the submit endpoint"
        return CheckResult(
            status=STATUS_ERROR,
            summary="הבדיקה נכשלה - לא התקבלה תשובה מהאתר",
            detail=detail,
        )

    errors = [str(e) for e in (submit.get("errors") or []) if e]
    input_errors = [str(e) for e in (submit.get("inputsErrors") or []) if e]
    warnings = [str(w) for w in (submit.get("warnings") or []) if w]

    if not submit.get("success"):
        # "Nothing owed" is reported as a failed submit with an explanatory
        # message, so it has to be told apart from a genuine error.
        if _looks_like_no_debt(errors + warnings):
            return CheckResult(status=STATUS_CLEAR, summary="לא נמצאו קנסות",
                               detail=" ".join(errors + warnings))
        if input_errors or _looks_like_mismatch(errors):
            # Re-submitting the same rejected numbers cannot start working.
            return CheckResult(
                status=STATUS_ERROR,
                summary="הפרטים נדחו על ידי האתר - בדוק ת.ז. ומספר רישיון",
                detail=" ".join(input_errors or errors),
                retryable=False,
            )
        return CheckResult(
            status=STATUS_ERROR,
            summary="הבדיקה נכשלה - האתר החזיר שגיאה",
            detail=" ".join(errors + warnings) or "unknown error",
        )

    fines: list[dict] = []
    for basket in collector.basket:
        fines.extend(_extract_fines(basket))

    if not fines:
        if collector.basket or _looks_like_no_debt(warnings):
            return CheckResult(status=STATUS_CLEAR, summary="לא נמצאו קנסות",
                               detail="basket returned no chargeable items")
        # Identification worked but the basket never arrived. Report a failure
        # rather than an all-clear, so a site change can never look like good news.
        return CheckResult(
            status=STATUS_ERROR,
            summary="הבדיקה נכשלה - ההזדהות הצליחה אך רשימת החובות לא נטענה",
            detail="no basket response captured",
        )

    total = round(sum(f["amount"] for f in fines), 2)
    return CheckResult(
        status=STATUS_FINES,
        summary=f"נמצאו {len(fines)} חובות בסך {total:,.2f} ש\"ח",
        fines=fines,
        total_amount=total,
    )


def _challenge_visible(page) -> bool:
    """True when reCAPTCHA has put an image challenge on screen."""
    try:
        height = page.evaluate(
            """() => {
                const f = [...document.querySelectorAll('iframe')]
                    .find(x => (x.src || '').includes('recaptcha/api2/bframe'));
                if (!f) return 0;
                return Math.round(f.getBoundingClientRect().height);
            }"""
        )
        return bool(height and height > 100)
    except PlaywrightError:
        return False


def browser_profile_dir(profile: str) -> Path:
    """Where this person's browser profile lives - one directory per person.

    Everyone used to share a single profile, which meant one browser session
    submitted several different identity documents minutes apart. That is the
    least human thing this tool does, and a shared cookie jar also lets one
    person's damaged reCAPTCHA score be inherited by the whole household.

    The directory is named after a hash of the nickname rather than the nickname
    itself, for two reasons: the nicknames here are real names, which have no
    business being written into a path; and a non-ASCII path handed to Chrome has
    been a dependable source of encoding failures on Windows.
    """
    digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()[:12]
    return DATA_DIR / f"browser-profile--{digest}"


def _claim_legacy_profile(target: Path) -> None:
    """Hand the old shared profile to the first person who asks for one.

    That directory holds whatever standing the tool has built up with reCAPTCHA,
    so it is moved rather than discarded. Everyone else starts with a fresh
    profile and may be challenged once while it settles. Copying it per person
    would be worse than a cold start: the same reCAPTCHA cookie appearing in two
    "different" browsers links them together anyway.
    """
    if target.exists() or not LEGACY_PROFILE_DIR.is_dir():
        return
    if any(DATA_DIR.glob("browser-profile--*")):
        return  # somebody has already claimed it
    try:
        LEGACY_PROFILE_DIR.rename(target)
        log.info("Reusing the previous shared browser profile for this person.")
    except OSError as exc:
        # Not fatal: a fresh profile is created below instead.
        log.debug("Could not reuse the shared browser profile: %s", exc)


def _launch(pw, headless: bool, profile: str):
    """Prefer the user's real Chrome; fall back to Playwright's bundled build."""
    profile_dir = browser_profile_dir(profile)
    _claim_legacy_profile(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    options = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        locale="he-IL",
        timezone_id="Asia/Jerusalem",
        viewport={"width": 1280, "height": 900},
    )
    try:
        return pw.chromium.launch_persistent_context(channel="chrome", **options)
    except PlaywrightError:
        log.debug("Google Chrome not available; using the bundled browser.")
        return pw.chromium.launch_persistent_context(**options)


def _run_once(credentials: Credentials, headless: bool, interactive: bool,
              profile: str) -> CheckResult:
    collector = _ResponseCollector()

    with sync_playwright() as pw:
        context = _launch(pw, headless, profile)
        page = None
        try:
            page = context.new_page()
            page.on("response", collector.handle)
            page.set_default_timeout(NAV_TIMEOUT_MS)

            page.goto(URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_selector("select", timeout=NAV_TIMEOUT_MS)
            page.select_option("select", SERVICE_VALUE)

            page.wait_for_selector(FIELD_ID, state="visible", timeout=NAV_TIMEOUT_MS)
            page.fill(FIELD_ID, credentials.id_number)
            page.fill(FIELD_LICENCE, credentials.license_number)
            page.click("input[type=submit]")

            deadline = time.monotonic() + SUBMIT_WAIT_MS / 1000
            asked_to_solve = False

            while collector.submit is None:
                if time.monotonic() > deadline:
                    if _challenge_visible(page):
                        if not interactive:
                            return CheckResult(
                                status=STATUS_CHALLENGE,
                                summary="נדרש אימות CAPTCHA - הבדיקה לא הושלמה",
                                detail=(
                                    "reCAPTCHA asked for an image challenge. Run "
                                    f"'{INVOCATION} check --all --if-stale 12' "
                                    "yourself and answer it in the window that "
                                    "opens. --profile is avoided here on purpose: "
                                    "a Hebrew nickname cannot be typed into a "
                                    "Windows console."
                                ),
                                retryable=False,
                            )
                        if not asked_to_solve:
                            asked_to_solve = True
                            deadline = time.monotonic() + SOLVE_WAIT_MS / 1000
                            log.warning(
                                "The site is asking for a CAPTCHA. Please answer it in "
                                "the browser window; waiting up to %d minutes.",
                                SOLVE_WAIT_MS // 60_000,
                            )
                            continue
                        return CheckResult(
                            status=STATUS_CHALLENGE,
                            summary="נדרש אימות CAPTCHA - לא נענה בזמן",
                            detail="the challenge was not answered in time",
                            retryable=False,
                        )
                    return CheckResult(
                        status=STATUS_ERROR,
                        summary="הבדיקה נכשלה - האתר לא הגיב בזמן",
                        detail="timed out waiting for the submit response",
                    )
                page.wait_for_timeout(500)

            if collector.submit.get("success"):
                try:
                    page.wait_for_response(
                        lambda r: any(e in r.url for e in BASKET_ENDPOINTS),
                        timeout=20_000,
                    )
                except PlaywrightTimeout:
                    log.debug("basket response did not arrive within 20s")
                page.wait_for_timeout(1_500)

            return _classify(collector)

        except PlaywrightTimeout:
            if page is not None and _challenge_visible(page):
                return CheckResult(
                    status=STATUS_CHALLENGE,
                    summary="נדרש אימות CAPTCHA - הבדיקה לא הושלמה",
                    detail="reCAPTCHA served an image challenge",
                    retryable=False,
                )
            return CheckResult(
                status=STATUS_ERROR,
                summary="הבדיקה נכשלה - האתר לא הגיב בזמן",
                detail="timed out waiting for the site",
            )
        except PlaywrightError as exc:
            return CheckResult(
                status=STATUS_ERROR,
                summary="הבדיקה נכשלה - שגיאת דפדפן",
                detail=str(exc)[:300],
            )
        finally:
            context.close()


def check(
    credentials: Credentials,
    profile: str,
    headless: bool = False,
    interactive: bool = True,
    attempts: int = 2,
) -> CheckResult:
    """Run the check, retrying transient failures with backoff.

    Rejected input and CAPTCHA challenges are not retried: hammering the form
    neither fixes wrong numbers nor improves a reCAPTCHA score. Those outcomes
    carry retryable=False, set where they are classified, so this loop never has
    to guess from the wording of a message.
    """
    result = CheckResult(status=STATUS_ERROR, summary="הבדיקה לא רצה", detail="")

    for attempt in range(1, attempts + 1):
        result = _run_once(credentials, headless=headless, interactive=interactive,
                           profile=profile)
        if result.ok or not result.retryable:
            return result
        if attempt < attempts:
            delay = 5 * attempt + random.uniform(0, 3)
            log.info("Attempt %d failed (%s); retrying in %.0fs",
                     attempt, result.detail, delay)
            time.sleep(delay)

    return result
