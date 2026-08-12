# HANDOFF — KnasWatch

## Purpose

כלי קוד פתוח שבודק אחת ליום קנסות תעבורה באתר רשות האכיפה והגבייה
(`https://ecom.gov.il/voucherspa/input/318`), לפי תעודת זהות + מספר רישיון נהיגה,
ושולח התראות בטלגרם. תומך בכמה פרופילים (בני משפחה) וכמה נמענים.
המטרה: להעלות ל-GitHub כך שכל אחד יתקין אצלו, בלי שרת מרכזי ובלי שפרטים
של אף אחד יגיעו למחשב של מישהו אחר.

## Key decisions

Python + Playwright, ולא קריאות API ישירות: האתר מוגן ב-invisible reCAPTCHA
ו-`PostIdentificationData` דורש `captchaToken` תקף. נבדק מול האתר החי.

הרצה מקומית בלבד, לא בענן: reCAPTCHA מדרג datacenter IP כחשוד, ולכן
GitHub Actions או VPS היו נכשלים. IP ביתי + דפדפן אמיתי עוברים בשקט.

דפדפן גלוי (headed) כברירת מחדל, עם persistent profile ו-Chrome אמיתי.
מצב headless מקבל CAPTCHA הרבה יותר. אין ולא יהיה פתרון CAPTCHA אוטומטי —
זה גם היה הופך את הפרויקט ללא ראוי לפרסום.

תדירות היא הגורם המרכזי: פנייה אחת ליום לכל פרופיל כמעט אף פעם לא מקבלת
אתגר. שלושה triggers ביום, אבל `--if-stale 20` גורם למאוחרים לצאת מיד
אם הבדיקה של היום כבר הצליחה — כלומר ביקור אחד ביום, עם גיבוי.

סודות ב-OS credential vault בלבד (`keyring`), אף פעם לא בקובץ. לכן ה-repo
בטוח לפרסום מעצם המבנה, לא בזכות `.gitignore`.

חיבור טלגרם דרך pairing code אקראי: רק הצ'אט ששולח בדיוק את הקוד מקושר.
בלי זה, מי שהודיע לבוט ברגע הלא נכון היה הופך לנמען. צ'אטים קבוצתיים נדחים.

רישיון MIT.

## Files touched

```
knaswatch/__init__.py       APP_NAME, INVOCATION (knaswatch.bat בחלונות)
knaswatch/__main__.py       CLI: setup/check/telegram/recipients/add-recipient/
                            remove-recipient/add-profile/rename-profile/
                            remove-profile/config/status/schedule/unschedule
knaswatch/checker.py        Playwright flow, _classify, CheckResult.retryable
knaswatch/notify.py         Telegram: send_to_chat/broadcast/pair_chat/format_result
knaswatch/vault.py          keyring: Credentials, TelegramConfig, Recipient
knaswatch/state.py          config.json/state.json ב-%LOCALAPPDATA%\KnasWatch
knaswatch/schedule.py       Windows Task Scheduler, parse_time, XML escaping
knaswatch/logging_setup.py  לוג עם redaction של רצף 7+ ספרות
knaswatch.bat               launcher + תפריט אינטראקטיבי (12 פריטים, 0=יציאה)
tests/                      test_validate, test_classify, test_notify,
                            test_recipients, test_regressions
README.md, SECURITY.md, LICENSE, requirements.txt, .gitignore
```

## Current state

עובד מקצה לקצה. שני פרופילים מוגדרים (שמות בעברית), שניהם החזירו
"לא נמצאו קנסות". טלגרם מחובר ונבדק. הודעת all-clear יומית מופעלת.

Scheduled task בשם `KnasWatch Daily Check` רשום ואומת מול המערכת האמיתית:
triggers ב-09:00/14:00/19:00 עם `RandomDelay=PT20M`, פעולה
`pythonw.exe -m knaswatch check --all --unattended --if-stale 20`,
רץ רק כשהמשתמש מחובר (לכן לא נשמרת סיסמת Windows).

50 טסטים עוברים ב-5 קבצים.

בוצע `git init -b main`, נוצר `LICENSE` (MIT), 20 קבצים ב-staging.
**עדיין לא בוצע commit** — ממתין ל-GitHub username כדי להגדיר
`git config --local user.email` לכתובת ה-noreply.

כל השמות הפרטיים האמיתיים הוסרו מהטסטים לפני ה-commit הראשון
(הוחלפו ב-`אבא`/`אמא`/Alice/Bob). סריקה חוזרת נקייה.

## Next steps

לקבל GitHub username, להגדיר identity לוקאלית, לבצע commit ראשון.

להוסיף `.gitattributes`: `*.bat text eol=crlf` והשאר מנורמל, כדי ש-clone
בלינוקס/מק לא ישבור את ה-launcher.

להחליט אם `HANDOFF.md` נכנס ל-`.gitignore` — כרגע הוא ייכלל ב-commit.

לאמת את הריצה האוטומטית האמיתית הראשונה מחר ב-09:00 (בדיקה + התראה).

להוסיף נמען שני עם `add-recipient`, ולהחליט אם למקד אותו לפרופיל אחד
(`--profile`) או להשאיר "כל הפרופילים".

## Open questions

מה ה-GitHub username / כתובת ה-noreply המדויקת.

האם ה-LICENSE יישא שם אמיתי במקום "KnasWatch contributors".

האם להוסיף heartbeat שבועי כחלופה להודעה יומית.

## Technical context

זיהוי באתר: `identification type = 10`, שדות `k_id_tz` (ת.ז.) ו-`k_id_num`
(רישיון). ה-submit הוא `PostIdentificationData`; התשובה נקראת דרך
response interception של Playwright, לא scraping.

הבחנה קריטית שנבדקה מול האתר החי — שתי הודעות שונות:
נקי = `חייב לא זוהה במערכת / לזיהוי זה אין תיקים פתוחים`,
פרטים שגויים = `אין התאמה בין מס. זהות ורישיון נהיגה`.
בלי ההבחנה הזו רשומה נקייה נראתה ככישלון, וגרוע מכך — טעות בת.ז. הייתה
עלולה להיראות כ"אין קנסות". `_MISMATCH_PHRASES` נבדק לפני `_NO_DEBT_PHRASES`.

`CheckResult.retryable` נקבע במקום הסיווג ולא לפי נוסח ההודעה. פרטים
שנדחו ו-CAPTCHA לא מנוסים שוב; שגיאה לא מוכרת כן.

התראה נשלחת **לפני** שמירת ה-fingerprint. אחרת כישלון שליחה חד-פעמי היה
משתיק לצמיתות דיווח על קנס אמיתי.

כל טקסט שמוזרק להודעת טלגרם עובר `html.escape` — `parse_mode=HTML`, ותיאורי
הקנסות מגיעים מהאתר. תו `<` בודד גרם ל-400 ולאובדן ההתראה.

אזהרה לגבי אבחון: כלי ה-shell של הסוכן רץ ב-sandbox, ותהליכים שמופעלים
מ-Task Scheduler ראו קובצי `%LOCALAPPDATA%` שונים. אימות של משימות מתוזמנות
חייב לרוץ עם `dangerouslyDisableSandbox`.

בדיקות מול האתר החי מורידות את דירוג ה-reCAPTCHA של הפרופיל. אחרי סדרת
בדיקות כדאי להמתין כמה שעות לפני ריצה אמיתית.
