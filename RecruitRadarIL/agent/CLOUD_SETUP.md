# Run in the cloud - free, autonomous, no laptop needed

The Telegram control bot (`telegram_bot.py`) is great, but it only lives while
your computer is on. This page makes the agent run **in GitHub's cloud** on a
schedule: it scores the corpus and pushes a digest to your Telegram chat twice
a day, even when the laptop is closed. It is free for public repositories.

Two layers, add them in order.

---

## עברית - הפעלה בענן (בחינם)

### רקע: איפה הבוט "רץ"?
הבוט האינטראקטיבי רץ על המחשב שלך. כשסוגרים את הלפטופ - הוא נעצר. הפתרון:
להריץ את המנוע ב-**GitHub Actions** (שרת חינמי של גיטהאב) לפי לוח זמנים, כך
שהוא סורק לבד ושולח לך דוח לטלגרם בלי שום מחשב דלוק אצלך.

> חשוב להבין את ההבדל:
> - **דוח מתוזמן בענן** (מה שמוגדר כאן) - עובד תמיד, גם כשהלפטופ סגור, אבל הוא
>   *דוחף* דוח בזמנים קבועים. אין פקודות אינטראקטיביות כמו `/scan` בזמן אמת.
> - **פקודות חיות מהטלפון** (`/scan`, `/top` מיד) - דורשות מכונה שדולקת תמיד.
>   בחינם: Oracle Cloud Always Free או Raspberry Pi בבית (ראה בסוף העמוד).

### שלב 1 - דוח אוטומטי לטלגרם (5 דקות, אפס חומרה)

1. **Secrets** (כבר הגדרת את שני הראשונים):
   ב-GitHub: `Settings → Secrets and variables → Actions → New repository secret`
   | שם הסוד | ערך |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | הטוקן מ-@BotFather |
   | `BOT_OWNER_ID` | ה-chat id שלך (המספר שהבוט החזיר על `/start`) |
   | `HASH_SALT` | *(מומלץ)* 64 תווים אקראיים - `python -c "import secrets;print(secrets.token_hex(32))"` |

2. **הפעלה ידנית לבדיקה:** לשונית `Actions` → `RecruitRadar-IL digest` →
   `Run workflow`. תוך כדקה אמור לנחות לך בטלגרם דוח (`/status` + top-10).
   *(עובד גם מאפליקציית GitHub בטלפון.)*

3. **לוח זמנים:** מרגע זה הוא רץ אוטומטית פעמיים ביום (08:00 ו-20:00 שעון
   ישראל). לשינוי - ערוך את שורת ה-`cron` ב-`.github/workflows/telegram-digest.yml`.

בשלב הזה הדוח מנקד את מה שכבר קיים ב-DB (או קורפוס הדגמה אם ריק). כדי שהוא
ינקד **נתונים אמיתיים שנאספים בענן** - עבור לשלב 2.

### שלב 2 - איסוף אוטומטי בענן (אופציונלי)

האיסוף דורש התחברות לחשבון הטלגרם שלך. בענן אין מי שיקליד קוד SMS, אז מפיקים
**StringSession** פעם אחת מקומית ושומרים אותה כסוד.

1. במחשב שלך, מתוך `RecruitRadarIL/`:
   ```
   pip install telethon
   python agent/collect_headless.py --login
   ```
   הזן api_id / api_hash / טלפון / קוד (ו-2FA אם יש). בסוף יודפס מחרוזת ארוכה.

2. הוסף שלושה סודות ל-Actions:
   | שם הסוד | ערך |
   |---|---|
   | `TELEGRAM_API_ID` | ה-api_id מ-my.telegram.org |
   | `TELEGRAM_API_HASH` | ה-api_hash |
   | `TELETHON_SESSION` | המחרוזת שהודפסה (סודית! כמו סיסמה) |

3. זהו. בכל ריצה ה-workflow יאסוף הודעות חדשות מהערוצים (רשימת הזרע +
   `channels_extra.txt`), ינקד, וישלח דוח על נתונים אמיתיים.

> ה-`TELETHON_SESSION` היא הרשאת גישה מלאה לחשבון. אל תדביק אותה בשום קובץ
> בריפו - רק בתיבת הסוד המוצפנת של GitHub.

### הערות
- הריפו ציבורי, לכן ה-DB **לעולם לא נשמר בתוכו** (יש בו תוכן הודעות). הוא נשמר
  ב-cache פרטי של Actions בין ריצות - "מאמץ מיטבי", לא ערובה.
- `data/verdicts.jsonl` **כן** מסונכרן דרך הריפו (ראה הלולאה עם הבוט המקומי
  ב-[README.md](README.md)) - זה מאפשר ל-LF4 ללמוד מ-verdicts שלך גם בענן.
- **אין LLM.** הניקוד מבוסס לגמרי על חוקי regex + IsolationForest + verdicts.
  שום מודל לא מסווג תוכן, שום מודל לא מאומן על ההודעות שלך.
- **מנגנון "sent"**: כל הודעה שנשלחה בעבר לא תישלח שוב אף פעם - נשמרת בטבלת
  `sent_leads`. הריצה הבאה תכלול רק **חדשות** מאז המשלוח האחרון.
- אין עלות: ל-public repos דקות Actions חינם.
- **למה לא Dify/n8n/LangChain?** הם כלי אורקסטרציה למי שאין לו pipeline.
  אצלך ה-pipeline כבר כתוב ב-Python (`pipeline.py`), והם לא פותרים את בעיית
  "מכונה שדולקת" - היו רק מוסיפים שכבה מיותרת.

### פקודות חיות 24/7 (אם תרצה יותר מדוח)
מכונה חינמית שדולקת תמיד ומריצה את `telegram_bot.py`:
- **Oracle Cloud Always Free** - VM חינמי לתמיד (ARM, עד 24GB RAM).
- **Raspberry Pi / מחשב ישן בבית** - הכי פשוט; הגדר את הבוט כשירות systemd.
מריצים שם `python agent/telegram_bot.py`, ומהטלפון שולחים `/scan` מתי שרוצים.

---

## English - quick reference

| File | Role |
|---|---|
| `.github/workflows/telegram-digest.yml` | Scheduled cloud job (twice daily + manual) |
| `agent/cloud_digest.py` | Scores the corpus and pushes the digest to Telegram |
| `agent/collect_headless.py` | Optional non-interactive collector (StringSession) |

**Layer 1 (push digest):** set `TELEGRAM_BOT_TOKEN` + `BOT_OWNER_ID` (and
`HASH_SALT`) as repository secrets → Actions tab → *Run workflow* to test →
it then runs on the cron schedule.

**Layer 2 (real collection):** run `python agent/collect_headless.py --login`
once locally, then add `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and
`TELETHON_SESSION` secrets. Collection self-skips if they are absent.

**Digest delivery:** each run sends **one CSV attachment** with only the leads
that (a) score `p_recruitment >= 0.5` and (b) have not been sent in any
previous digest. Once a lead ships, it is recorded in `sent_leads` and never
resurfaces. Silent runs (no new leads) send nothing at all. No LLM is
involved in the decision - scoring is rule- and statistics-based only.

**Live interactive commands** (`/scan` on demand) need an always-on host -
Oracle Cloud Always Free or a Raspberry Pi running `agent/telegram_bot.py`.
