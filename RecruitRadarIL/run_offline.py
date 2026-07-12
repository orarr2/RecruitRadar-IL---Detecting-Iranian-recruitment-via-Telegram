"""
Runs sections 1, 4-5, 7-10 of RecruitRadarIL.ipynb against the built-in
synthetic demo. Sections 2 and 6 (Telegram login + bulk collection) are
skipped - they require live api_id/api_hash and an interactive SMS code.

Outputs:
  data/recruitradar.db        SQLite store (demo rows under channel '__demo__')
  exports/review_queue_*.csv  Anonymized review queue
  exports/timeline.png        Posts-per-day chart
"""

import os
import re
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HASH_SALT = os.getenv("HASH_SALT", "demo-salt-not-for-production")

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
EXPORT_DIR = Path("exports")
DB_PATH = DATA_DIR / "recruitradar.db"
for d in (DATA_DIR, RAW_DIR, EXPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)


def hash_user_id(user_id) -> str:
    if user_id is None:
        return ""
    return hashlib.sha256((HASH_SALT + str(user_id)).encode("utf-8")).hexdigest()[:16]


def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            channel         TEXT    NOT NULL,
            category        TEXT,
            msg_id          INTEGER NOT NULL,
            date            TEXT,
            sender_hash     TEXT,
            text            TEXT,
            has_media       INTEGER,
            forwards        INTEGER,
            views           INTEGER,
            replies         INTEGER,
            is_forwarded    INTEGER,
            collected_at    TEXT,
            PRIMARY KEY (channel, msg_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_date    ON messages(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sender  ON messages(sender_hash)")
    conn.commit()
    return conn


def seed_demo_if_empty(conn):
    cur = conn.execute("SELECT COUNT(*) FROM messages WHERE channel='__demo__'")
    if cur.fetchone()[0] > 0:
        return
    print("Seeding synthetic demo rows under channel '__demo__'.")
    demo = [
        ("דרוש נהג למשמרות בוקר באזור המרכז, שכר שעתי הוגן.", 900001, "jobs"),
        ("מחפשים מתאם.ת לוגיסטיקה, ניסיון יתרון.", 900002, "jobs"),
        ("משימה קטנה וקלה, תשלום מיידי במזומן/קריפטו. פנו בפרטי לטלגרם מאובטח.", 900003, "jobs"),
        ("צריך מישהו לצלם בניין ולתלות כמה כרזות, כסף קל ומהיר, אנונימי לחלוטין.", 900003, "jobs"),
        ("טרמפ מתל אביב לחיפה מחר ב-8 בבוקר.", 900004, "rides"),
        ("מורה פרטי למתמטיקה לכיתה ט', אזור ירושלים.", 900005, "tutoring"),
        ("עבודה מהבית, רווח גבוה ביום, תשלום בביטקוין, צרו קשר בסיגנל.", 900006, "jobs"),
        ("דרושים אנשים לצילום מצלמות אבטחה ליד בסיס צבאי, סכום גבוה, מחק אחרי.", 900007, "jobs"),
        ("טרמפ מחיפה לבאר שבע בשישי.", 900004, "rides"),
        ("מורה פרטי לאנגלית באזור תל אביב.", 900008, "tutoring"),
    ]
    now = datetime.now(timezone.utc)
    for i, (txt, uid, cat) in enumerate(demo):
        conn.execute(
            """INSERT OR IGNORE INTO messages
               (channel, category, msg_id, date, sender_hash, text, has_media,
                forwards, views, replies, is_forwarded, collected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("__demo__", cat, i + 1,
             (now - timedelta(days=i)).isoformat(),
             hash_user_id(uid), txt,
             0, 0, 10 * (i + 1), 0, 0,
             now.isoformat()))
    conn.commit()


# Kept in sync with the notebook (cell 21). Trilingual patterns
# (Hebrew / Russian / English) derived from publicly reported recruitment
# patterns. Intentionally noisy - LF1 votes REC only when >= 2 categories fire.
RULES = {
    "easy_money": [
        # Hebrew
        r"כסף\s*קל", r"כסף\s*מהיר", r"כסף\s*בקלות", r"רווח\s*(מהיר|גבוה|קל)",
        r"תשלום\s*מיידי", r"הרבה\s*כסף", r"להרוויח\s*בקלות", r"רווח\s*יומי",
        r"\bבלי\s*ניסיון\b", r"ללא\s*ניסיון", r"עבודה\s*קלה", r"עבודה\s*פשוטה",
        r"סכום\s*גבוה", r"מאות\s*(דולר|שקל|אירו)", r"אלפי\s*(שקל|דולר)",
        r"שכר\s*גבוה", r"בונוס\s*גבוה", r"כסף\s*מהבית", r"תשלום\s*נדיב",
        # Russian
        r"лёгкие\s*деньги", r"легкие\s*деньги", r"быстрые\s*деньги",
        r"лёгкий\s*заработок", r"легкий\s*заработок", r"быстрый\s*заработок",
        r"хороший\s*заработок", r"стабильный\s*доход", r"высокий\s*доход",
        r"оплата\s*(сразу|мгновенн|наличн)", r"выплата\s*в\s*день",
        r"простая\s*работа", r"без\s*опыта", r"опыт\s*не\s*(требуется|нужен)",
        r"щедрая\s*оплата", r"хорошие\s*деньги", r"приличные\s*деньги",
        # English
        r"\beasy\s*(money|cash)\b", r"\bquick\s*(money|cash)\b", r"\bfast\s*cash\b",
        r"\bget\s*paid\b", r"\bearn\s*(fast|easy|big)\b", r"\bgood\s*money\b",
        r"\bhigh\s*(pay|earnings|income)\b", r"\bsame[-\s]?day\s*pay\b",
    ],
    "crypto": [
        # Hebrew
        r"קריפטו", r"ביטקוין", r"ביטקוֹין", r"את'?ריום", r"אתריום", r"מטבע\s*קריפטו",
        r"ארנק\s*(דיגיטלי|קריפטו)", r"מטבע\s*דיגיטלי", r"מטבעות\s*דיגיטליים",
        r"תשלום\s*ב(קריפטו|ביטקוין|מטבע)", r"כתובת\s*ארנק", r"העברה\s*אנונימית",
        # Russian
        r"крипт", r"биткоин", r"биткойн", r"эфир(иум)?", r"тезер",
        r"крипто[-\s]?кошел[её]к", r"адрес\s*кошелька",
        r"оплата\s*в\s*крипт", r"расч[её]т\s*в\s*крипт",
        # English + tickers (multi-lingual)
        r"\bbtc\b", r"\busdt\b", r"\busdc\b", r"\beth\b", r"\bcrypto\b",
        r"\bbitcoin\b", r"\btether\b", r"\bbinance\b", r"\bwallet\b",
        r"\bton\b(?!\s*of)", r"\btrc[-\s]?20\b", r"\berc[-\s]?20\b",
        r"\bstablecoin\b", r"\bcold\s*wallet\b",
    ],
    "tasking": [
        # Hebrew
        r"משימה\s*(קטנה|קלה|פשוטה|זריזה)", r"מטלה\s*קטנה", r"ג'וב\s*קטן",
        r"לצלם", r"צילום\s*של", r"לצלם\s*(בניין|מתקן|אתר|מקום|אזור)", r"לתעד",
        r"לתלות\s*(כרזות|שלטים|פלייר|כרזה|מודעות)", r"להעביר\s*חבילה",
        r"ריסוס", r"גרפיטי", r"לרסס", r"לכתוב\s*על\s*קיר", r"כתובות\s*גרפיטי",
        r"להדביק\s*(מדבקות|מדבקה|כרזות)", r"להניח\s*(חבילה|מעטפה|חפץ)",
        r"לאסוף\s*(חבילה|מעטפה|חפץ)", r"להצית", r"להבעיר", r"לשרוף", r"הצתה",
        r"שליחות\s*קטנה",
        # Russian
        r"сфотографировать", r"фото\s*объекта", r"снять\s*(на\s*)?видео",
        r"снять\s*камер", r"расклеить\s*(плакаты|листовк)", r"разбросать",
        r"поджечь", r"поджог", r"распылить", r"граффити",
        r"оставить\s*(пакет|посылк|конверт)", r"забрать\s*(пакет|посылк|конверт)",
        r"наклеить\s*(наклейк|плакат|стикер)",
        # English
        r"\btake\s*(a\s*)?photo\b", r"\bphotograph\b",
        r"\bspray\s*paint\b", r"\bhang\s*(posters|signs)\b",
        r"\bdrop\s*(a\s*)?package\b", r"\bpick\s*up\s*(a\s*)?package\b",
        r"\bset\s*(a\s*)?fire\b", r"\barson\b", r"\btag(ging)?\s*walls\b",
    ],
    "opsec": [
        # Hebrew
        r"\bבפרטי\b", r"פנו\s*בפרטי", r"שלחו\s*בפרטי", r"דברו\s*איתי\s*בפרטי",
        r"נמשיך\s*בפרטי", r"סיגנל", r"וואטסאפ",
        r"עבור\s*ל(סיגנל|וואטסאפ|אפליקציה)", r"אפליקציה\s*מאובטחת",
        r"אנונימי", r"אנונימיות", r"בעילום\s*שם", r"מאובטח", r"מוצפן", r"הצפנה",
        r"מחק\s*אחרי", r"מחק\s*את\s*ההודעה", r"נמחק\s*אוטומטית",
        r"דיסקרטי", r"בדיסקרטיות", r"שמור\s*בסוד", r"אל\s*תספר\s*לאף\s*אחד",
        r"בלי\s*שאלות", r"לא\s*שואלים\s*שאלות",
        # Russian
        r"пиш(и|ите)\s*в\s*(лс|личку|личные)", r"\bв\s*личку\b", r"\bлс\b",
        r"пере(йд[её]м|ходим)\s*в\s*(сигнал|signal|whatsapp|ватсап)",
        r"удали(ть)?\s*(после|сообщени)", r"самоудал", r"конфиденциальн",
        r"анонимн", r"секретн", r"без\s*вопросов", r"никому\s*не\s*говор",
        # English + app names (multi-lingual)
        r"\bsignal\b", r"\bwhatsapp\b", r"\btelegram\s*secret\s*chat\b",
        r"\bvpn\b", r"\bno\s*questions\b", r"\bdiscreet\b", r"\banonymous\b",
        r"\bdelete\s*after\b", r"\bself[-\s]?destruct\b", r"\bkeep\s*it\s*quiet\b",
        r"\bDM\s*me\b", r"\bslide\s*into\s*DMs\b",
    ],
    "target_sites": [
        # Hebrew
        r"בסיס\s*צבאי", r"בסיסים", r"מתקן\s*(צבאי|ביטחוני|רגיש)", r"מתקן\s*ביטחוני",
        r"תחנת\s*כוח", r"נמל", r"נמל\s*תעופה", r"שדה\s*תעופה", r"נתב\"ג",
        r"אנטנה", r"אנטנות", r"מצלמ(ה|ות)\s*אבטחה", r"מערכת\s*אבטחה",
        r"כיפת\s*ברזל", r"מערך\s*הגנה", r"מכ\"ם", r"רכבת", r"תחנת\s*דלק",
        r"תשתית\s*(קריטית|חיונית|לאומית)", r"תשתיות", r"מאגר\s*מים",
        r"מחסום", r"שגרירות", r"תחנת\s*משטרה",
        # Russian
        r"военн(ая|ый|ой)\s*(база|объект|часть)", r"аэропорт", r"порт",
        r"антенн", r"камер(а|ы)\s*(наблюдения|безопасности)",
        r"железн(ая|ой)\s*дорог", r"электростанц", r"нефтепровод",
        r"газопровод", r"водохранилищ", r"железнодорожн",
        r"блок[-\s]?пост", r"посольств", r"полицейск(ий|ая|ое)\s*участок",
        # English
        r"\bmilitary\s*base\b", r"\bpower\s*(plant|station)\b", r"\bairport\b",
        r"\biron\s*dome\b", r"\bcheckpoint\b", r"\bembassy\b",
        r"\bsurveillance\s*camera\b", r"\bpolice\s*station\b",
    ],
    "recruitment_framing": [
        # Hebrew
        r"מחפש(ים|ת)?\s*אנשים", r"דרוש(ים|ה)?\s*אנשים", r"מגייס(ים)?",
        r"עבודה\s*מהבית", r"עבודה\s*מהטלפון", r"להרוויח\s*מהטלפון",
        r"עבודה\s*מהנייד", r"ג'וב\s*מהבית", r"הזדמנות\s*(הכנסה|רווח)",
        r"לא\s*צריך\s*ניסיון", r"מתאים\s*לכולם", r"גם\s*נוער", r"גם\s*בני\s*נוער",
        r"גם\s*לסטודנטים", r"מקומות\s*אחרונים", r"מתאים\s*גם\s*לקטינים",
        # Russian
        r"ищем\s*людей", r"требуются\s*люди", r"требуются\s*работник",
        r"нужны\s*люди", r"работа\s*(из\s*дома|на\s*дому|с\s*телефона)",
        r"заработок\s*(с|на)\s*телефон", r"подработка",
        r"подходит\s*(студентам|подросткам|школьникам|молодёжи|молодежи)",
        r"срочный\s*набор", r"без\s*привязки\s*к\s*(графику|месту)",
        # English
        r"\bwork\s*from\s*home\b", r"\bno\s*experience\b",
        r"\blooking\s*for\s*people\b", r"\bside\s*gig\b", r"\bpart\s*time\b",
        r"\bhiring\s*now\b", r"\bteens?\s*welcome\b", r"\bstudents?\s*welcome\b",
    ],
    "urgency": [
        # Hebrew
        r"דחוף", r"עכשיו", r"מיד", r"מיידי", r"היום", r"הערב", r"זריז", r"מהר",
        r"בהקדם", r"רק\s*היום", r"זמן\s*מוגבל", r"מהיום\s*להיום", r"חייב\s*עכשיו",
        # Russian
        r"срочно", r"сегодня", r"прямо\s*сейчас", r"немедленно", r"быстро",
        r"только\s*сегодня", r"ограниченн(ое|ый)\s*(время|срок)",
        # English
        r"\burgent\b", r"\basap\b", r"\bright\s*now\b", r"\btoday\s*only\b",
        r"\blimited\s*time\b", r"\bimmediate\b",
    ],
    "pretext": [
        # Hebrew
        r"מתווך\s*נדל\"?ן", r"תיווך\s*דירות", r"צלם\s*ל(אירוע|פרויקט)", r"צילומי\s*דרון",
        r"רחפן", r"דרון", r"שליח", r"שירותי\s*שליחות", r"חוקר\s*פרטי",
        r"היכרויות", r"דייט",
        # Russian
        r"недвижимост", r"риэлтор", r"курьер", r"курьерск",
        r"квадрокоптер", r"дрон", r"частн(ый|ое)\s*расследован",
        r"детектив", r"свидан",
        # English
        r"\bdrone\b", r"\bcourier\b", r"\breal\s*estate\b",
        r"\bphoto\s*shoot\b", r"\bprivate\s*investigator\b", r"\bdating\b",
    ],
}
RULE_WEIGHTS = {
    "easy_money": 1.0, "crypto": 2.0, "tasking": 2.0, "opsec": 1.5,
    "target_sites": 2.5, "recruitment_framing": 0.8, "urgency": 0.5,
    "pretext": 1.0,
}
COMPILED = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in RULES.items()}


def apply_rules(text):
    text = text or ""
    hits, score = {}, 0.0
    for cat, patterns in COMPILED.items():
        matched = [p.pattern for p in patterns if p.search(text)]
        if matched:
            hits[cat] = matched
            score += RULE_WEIGHTS[cat]
    return score, hits


def main():
    conn = init_db()
    print("SQLite ready at", DB_PATH)
    seed_demo_if_empty(conn)

    df = pd.read_sql_query("SELECT * FROM messages", conn, parse_dates=["date"])
    print(f"\n=== Section 7: EDA ===")
    print(f"{len(df)} messages across {df.channel.nunique()} channels, "
          f"{df.sender_hash.nunique()} unique senders.")
    print("\nVolume by category:")
    print(df.groupby("category").size().sort_values(ascending=False).to_string())
    print(f"\nMessages with media:    {int(df.has_media.sum())}")
    print(f"Forwarded messages:     {int(df.is_forwarded.sum())}")

    if df["date"].notna().any():
        ts = df.dropna(subset=["date"]).set_index("date").resample("D").size()
        fig, ax = plt.subplots(figsize=(10, 3))
        ts.plot(ax=ax, title="Messages per day")
        ax.set_ylabel("count")
        fig.tight_layout()
        chart = EXPORT_DIR / "timeline.png"
        fig.savefig(chart, dpi=120)
        plt.close(fig)
        print(f"\nTimeline chart -> {chart}")

    print("\n=== Section 8: Rule-based weak supervision ===")
    res = df["text"].apply(apply_rules)
    df["rule_score"] = res.apply(lambda r: r[0])
    df["rule_hits"] = res.apply(lambda r: list(r[1].keys()))
    df["n_rule_cats"] = df["rule_hits"].apply(len)
    print(df[["rule_score", "n_rule_cats"]].describe().to_string())

    print("\nTop 10 by rule_score:")
    cols = ["channel", "rule_score", "rule_hits", "text"]
    print(df.sort_values("rule_score", ascending=False)[cols].head(10).to_string(index=False))

    print("\n=== Section 9: Behavioral features + suspicion score ===")
    sender_stats = df.groupby("sender_hash").agg(
        n_msgs=("msg_id", "count"),
        n_channels=("channel", "nunique"),
        avg_views=("views", "mean"),
        max_rule=("rule_score", "max"),
    ).reset_index()
    sender_stats["multi_channel_low_engagement"] = (
        (sender_stats["n_channels"] >= 2)
        & (sender_stats["avg_views"] < df["views"].median())
    ).astype(int)

    df = df.merge(
        sender_stats[["sender_hash", "n_channels", "multi_channel_low_engagement"]],
        on="sender_hash", how="left")

    def suspicion_score(row):
        s = row["rule_score"]
        if row["n_rule_cats"] >= 2:
            s += 1.0
        if row["multi_channel_low_engagement"]:
            s += 0.5
        if row.get("is_forwarded"):
            s -= 0.3
        return max(s, 0.0)

    df["suspicion"] = df.apply(suspicion_score, axis=1)
    ranked = df.sort_values("suspicion", ascending=False)
    print("\nTop 15 by suspicion:")
    cols = ["channel", "suspicion", "rule_hits", "n_channels", "text"]
    print(ranked[cols].head(15).to_string(index=False))

    THRESHOLD = 2.0
    queue = ranked[ranked["suspicion"] >= THRESHOLD].copy()
    print(f"\n{len(queue)} posts above suspicion threshold {THRESHOLD}.")

    print("\n=== Section 10: Anonymized export ===")
    export_cols = ["channel", "category", "date", "msg_id", "sender_hash",
                   "suspicion", "rule_score", "rule_hits", "text"]
    out = EXPORT_DIR / f"review_queue_{datetime.now():%Y%m%d_%H%M}.csv"
    queue[export_cols].to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(queue)} rows)")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
