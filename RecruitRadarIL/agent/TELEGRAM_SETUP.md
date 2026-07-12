# Telegram bot - setup guide / מדריך הפעלה

Drive the whole pipeline from the Telegram app on your phone. The bot runs on
your computer (the "brain"); the phone is the remote control. It works from
anywhere as long as the computer is on and online - no public IP, no port
forwarding.

---

## עברית - מדריך הפעלה

### מה זה עושה
הבוט מאפשר לך להריץ את הסוכן ולקבל את התוצאות ישירות מאפליקציית טלגרם בטלפון.
הבוט רץ על המחשב שלך (הוא ה"מוח"), והטלפון הוא השלט. עובד מכל מקום כל עוד המחשב
דלוק ומחובר לאינטרנט - בלי IP ציבורי ובלי הגדרות רשת.

### הכנה חד-פעמית (בערך 3 דקות)

1. **התקנת התלויות** (פעם אחת). מתוך התיקייה `RecruitRadarIL/`:
   ```
   pip install -r agent/requirements.txt
   ```

2. **יצירת בוט בטלגרם:**
   - פתח טלגרם וחפש את `@BotFather`
   - שלח לו `/newbot`
   - עקוב אחרי ההוראות (בחר שם לבוט, ואז שם משתמש שחייב להסתיים ב-`bot`)
   - הוא ייתן לך **טוקן** - משהו כמו `123456789:ABCdef...`. העתק אותו.

3. **הכנסת הטוקן להגדרות:**
   - העתק את `agent/.env.example` ל-`agent/.env` (אם עדיין לא קיים)
   - פתח את `agent/.env` והדבק את הטוקן בשורה:
     ```
     TELEGRAM_BOT_TOKEN=123456789:ABCdef...
     ```

4. **הרצת הבוט.** מתוך `RecruitRadarIL/`:
   ```
   python agent/telegram_bot.py
   ```
   אתה אמור לראות: `Bot @<שם_הבוט> is up.`

5. **נעילת הבוט אליך בלבד:**
   - בטלגרם, שלח לבוט שלך `/start`
   - הוא יחזיר לך את ה-`chat id` שלך (מספר)
   - הדבק אותו ב-`agent/.env`:
     ```
     BOT_OWNER_ID=<המספר>
     ```
   - עצור את הבוט (`Ctrl+C`) והרץ אותו שוב. מעכשיו רק אתה יכול לשלוט בו.

### שימוש יומיומי (מהטלפון)

| פקודה | מה עושה |
|---|---|
| `/scan` | סורק ומדרג מחדש, ושולח **קובץ CSV** עם ההודעות החשודות החדשות בלבד (שלא נשלחו בעבר) |
| `/top 10` | תצוגה מקדימה טקסטואלית של 10 החשודות המובילות (לא מסמן כ-"נשלח") |
| `/proposals` | ערוצים חדשים שהתגלו וממתינים לאישור |
| `/approve שם_ערוץ` | מאשר ערוץ (נכנס לרשימת האיסוף בפעם הבאה) |
| `/reject שם_ערוץ` | דוחה ערוץ |
| `/status` | סיכום הריצה האחרונה |
| `/help` | רשימת הפקודות |

הודעה שנשלחה ב-CSV אחד לא תישלח שוב לעולם - הרצה הבאה תכלול רק **חדשות** מאז.

### אם משהו לא עובד

- `TELEGRAM_BOT_TOKEN is not set` - לא הכנסת טוקן ל-`agent/.env`.
- `Telegram rejected the token` - הטוקן שגוי; בדוק שהעתקת אותו במלואו מ-BotFather.
- הבוט לא עונה - ודא שהסקריפט עדיין רץ במחשב ושהמחשב מחובר לאינטרנט.

### חשוב לזכור

- הבוט הוא רק שלט + תיבת דואר. הסריקה עצמה רצה **מקומית** על המחשב שלך.
- אם המחשב כבוי - הבוט לא יענה. להרצה גם כשהמחשב כבוי, ראה את הקטע על
  Task Scheduler / Raspberry Pi ב-[`README.md`](README.md).
- כל פלט הוא **רמז לבדיקה, לא מסקנה**.

---

## English - setup guide

### What it does
The bot lets you run the agent and read the results straight from the Telegram
app on your phone. The bot runs on your computer (the brain); the phone is the
remote. It works from anywhere while the computer is on and online.

### One-time setup (~3 minutes)

1. **Install dependencies** (once), from `RecruitRadarIL/`:
   ```
   pip install -r agent/requirements.txt
   ```

2. **Create a bot in Telegram:**
   - Open Telegram, search for `@BotFather`.
   - Send `/newbot`.
   - Follow the prompts (a display name, then a username ending in `bot`).
   - It gives you a **token** like `123456789:ABCdef...`. Copy it.

3. **Add the token to config:**
   - Copy `agent/.env.example` to `agent/.env` (if it does not exist yet).
   - Open `agent/.env` and paste:
     ```
     TELEGRAM_BOT_TOKEN=123456789:ABCdef...
     ```

4. **Run the bot**, from `RecruitRadarIL/`:
   ```
   python agent/telegram_bot.py
   ```
   You should see: `Bot @<your_bot> is up.`

5. **Lock the bot to you only:**
   - In Telegram, send your bot `/start`.
   - It replies with your `chat id` (a number).
   - Paste it into `agent/.env`:
     ```
     BOT_OWNER_ID=<the number>
     ```
   - Stop the bot (`Ctrl+C`) and start it again. Now only you can drive it.

### Daily use (from your phone)

| Command | Does |
|---|---|
| `/scan` | Re-score and deliver a **CSV** of the new flagged leads (marks them as sent so they never re-appear). |
| `/top 10` | Text preview of the top 10 flagged messages. Does NOT mark them as sent. |
| `/proposals` | Pending channel-discovery proposals. |
| `/approve NAME` | Approve a proposed channel. |
| `/reject NAME` | Reject a proposed channel. |
| `/status` | Summary of the last run. |
| `/help` | Command list. |

A message shipped in a CSV is never shipped again - the next run only carries
what's new since the previous delivery.

### Troubleshooting

- `TELEGRAM_BOT_TOKEN is not set` - no token in `agent/.env`.
- `Telegram rejected the token` - wrong token; re-copy it in full from BotFather.
- Bot silent - make sure the script is still running and the machine is online.

### Remember

- The bot is only a remote + inbox; scoring runs locally on your machine.
- If the computer is off, the bot cannot answer. For always-on operation see the
  Task Scheduler / Raspberry Pi notes in [`README.md`](README.md).
- Every output is a lead for review, not a conclusion.
