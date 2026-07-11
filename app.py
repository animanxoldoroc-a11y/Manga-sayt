# -*- coding: utf-8 -*-
"""
==========================================================================
  MANGA OLAMI  —  to'liq bitta fayldagi manga/manhwa o'qish sayti
==========================================================================
  Texnologiya : Python + Flask + SQLite
  PDF -> sahifa : PyMuPDF (requirements.txt ga qo'shilgan)
  Kirish       : Telefon raqam + Telegram bot orqali kod (parolsiz)
                 + admin uchun zaxira parol
  Muallif      : Ayubxon (ANIMAN)
  Railway (2026) uchun moslashtirilgan
==========================================================================
  SOZLASH (Railway "Variables"):
    TELEGRAM_BOT_TOKEN    -> @BotFather dan olingan bot tokeni
    TELEGRAM_BOT_USERNAME -> bot useri (@ belgisisiz, masalan: manga_olami_bot)
    ADMIN_PHONE           -> admin telefon raqami (masalan: 998901234567)
  Deploy qilingandan keyin zaxira admin bilan kirib, bir marta
  /tg/set-webhook sahifasini oching — bot ishga tushadi.
==========================================================================
"""

import os
import json
import sqlite3
import secrets
import hashlib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, request, session, redirect, url_for, g, jsonify,
    render_template_string, flash, abort, send_from_directory,
)
from werkzeug.security import generate_password_hash, check_password_hash

# ------------------------------------------------------------------ SOZLAMALAR
SITE_NAME = "Manga olami"
TELEGRAM_ADMIN = "https://t.me/animan_only"
COIN_NAME = "tanga"

# Zaxira admin (bot sozlanmagan bo'lsa ham kira olishingiz uchun)
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# --- TELEGRAM ORQALI KIRISH SOZLAMALARI ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "").strip()

# Webhook uchun maxfiy yo'l (token asosida)
TG_SECRET = hashlib.sha256((TELEGRAM_BOT_TOKEN or "no-token").encode()).hexdigest()[:24]

CODE_TTL_MINUTES = 5      # kod amal qilish vaqti
CODE_MAX_ATTEMPTS = 5     # nechta marta xato kiritish mumkin

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# RAILWAY: ma'lumotlar o'chib ketmasligi uchun "Volume" ulab, VOLUME_PATH bering.
RAILWAY_VOLUME = os.environ.get("VOLUME_PATH", BASE_DIR)
DB_PATH = os.path.join(RAILWAY_VOLUME, "manga_olami.db")
UPLOAD_DIR = os.path.join(RAILWAY_VOLUME, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "manga-olami-super-secret-key-12345")
_max_mb = int(os.environ.get("MAX_UPLOAD_MB", "128"))
app.config["MAX_CONTENT_LENGTH"] = _max_mb * 1024 * 1024

# Sessiya uzoq saqlanadi — foydalanuvchi "Chiqish" bosmaguncha akkaunt turadi
app.permanent_session_lifetime = timedelta(days=365)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


# ============================================================ MA'LUMOTLAR BAZASI
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_columns(db):
    """Eski bazaga yangi ustunlarni xavfsiz qo'shish (migratsiya)."""
    have = {r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()}
    for col, ddl in (("email", "email TEXT"),
                     ("google_id", "google_id TEXT"),
                     ("avatar", "avatar TEXT"),
                     ("phone", "phone TEXT")):
        if col not in have:
            db.execute(f"ALTER TABLE users ADD COLUMN {ddl}")


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT UNIQUE NOT NULL,
            password     TEXT NOT NULL DEFAULT '',
            email        TEXT,
            google_id    TEXT,
            avatar       TEXT,
            phone        TEXT,
            coins        INTEGER DEFAULT 0,
            is_admin     INTEGER DEFAULT 0,
            created_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS manga (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            slug         TEXT UNIQUE NOT NULL,
            description  TEXT,
            cover        TEXT,
            author       TEXT,
            genres       TEXT,
            status       TEXT DEFAULT 'Davom etadi',
            rating       REAL DEFAULT 0,
            created_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS chapters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            manga_id     INTEGER NOT NULL,
            number       REAL NOT NULL,
            title        TEXT,
            is_premium   INTEGER DEFAULT 0,
            coin_cost    INTEGER DEFAULT 0,
            created_at   TEXT,
            FOREIGN KEY (manga_id) REFERENCES manga(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS pages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id   INTEGER NOT NULL,
            page_number  INTEGER NOT NULL,
            image        TEXT NOT NULL,
            FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            chapter_id   INTEGER NOT NULL,
            created_at   TEXT,
            UNIQUE(user_id, chapter_id)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            amount       INTEGER NOT NULL,
            admin_id     INTEGER,
            note         TEXT,
            created_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS bookmarks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            manga_id     INTEGER NOT NULL,
            UNIQUE(user_id, manga_id)
        );

        -- Telegram bot bilan bog'langan telefon raqamlar
        CREATE TABLE IF NOT EXISTS tg_links (
            phone        TEXT PRIMARY KEY,
            chat_id      INTEGER NOT NULL,
            created_at   TEXT
        );

        -- Kirish kodlari
        CREATE TABLE IF NOT EXISTS login_codes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            phone        TEXT NOT NULL,
            code         TEXT NOT NULL,
            expires_at   TEXT NOT NULL,
            attempts     INTEGER DEFAULT 0
        );

        -- Sayt sozlamalari (standart avatar, ijtimoiy tarmoqlar, adminlar)
        CREATE TABLE IF NOT EXISTS settings (
            key          TEXT PRIMARY KEY,
            value        TEXT
        );
        """
    )
    db.commit()
    ensure_columns(db)
    db.commit()

    # Zaxira admin (parol bilan)
    cur = db.execute("SELECT id FROM users WHERE username=?", (ADMIN_LOGIN,))
    if cur.fetchone() is None:
        db.execute(
            "INSERT INTO users (username, password, coins, is_admin, created_at) "
            "VALUES (?,?,?,?,?)",
            (ADMIN_LOGIN, generate_password_hash(ADMIN_PASSWORD), 0, 1, now()),
        )
        db.commit()

    # Demo mangalar (faqat baza bo'sh bo'lsa)
    if db.execute("SELECT COUNT(*) FROM manga").fetchone()[0] == 0:
        seed_demo(db)

    db.close()


def slugify(text):
    keep = "abcdefghijklmnopqrstuvwxyz0123456789"
    s = "".join(c if c in keep else "-" for c in text.lower())
    while "--" in s:
        s = s.replace("--", "-")
    s = s.strip("-") or "manga"
    return s + "-" + secrets.token_hex(3)


def seed_demo(db):
    demo = [
        ("Regressor Yo'riqnomasi", "Sarguzasht, Jangari", 10.0,
         "Regressor sifatida qaytgan qahramon o'z bilimlari bilan dunyoni qutqaradi."),
        ("Muzli Sarhad", "Sarguzasht, Fantaziya", 9.4,
         "Abadiy qish qoplagan sarhadlarda omon qolish uchun kurash."),
        ("Qotil Piter", "Maktab hayoti, Komediya", 9.2,
         "Oddiy o'quvchidek ko'ringan, lekin sirli o'tmishga ega yigit haqida."),
    ]
    for i, (title, genres, rating, desc) in enumerate(demo, 1):
        cover = f"https://picsum.photos/seed/manga{i}/400/560"
        slug = slugify(title)
        db.execute(
            "INSERT INTO manga (title, slug, description, cover, author, genres, "
            "status, rating, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (title, slug, desc, cover, "Noma'lum", genres, "Davom etadi", rating, now()),
        )
        mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        for ch in range(1, 4):
            premium = 1 if ch == 3 else 0
            cost = 5 if premium else 0
            db.execute(
                "INSERT INTO chapters (manga_id, number, title, is_premium, "
                "coin_cost, created_at) VALUES (?,?,?,?,?,?)",
                (mid, ch, f"{ch}-bob", premium, cost, now()),
            )
            cid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            for pg in range(1, 5):
                img = f"https://picsum.photos/seed/m{i}c{ch}p{pg}/800/1200"
                db.execute(
                    "INSERT INTO pages (chapter_id, page_number, image) VALUES (?,?,?)",
                    (cid, pg, img),
                )
    db.commit()


# ================================================================ YORDAMCHILAR
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def do_login(uid):
    """Foydalanuvchini kiritish — sessiya 'Chiqish' bosilmaguncha saqlanadi."""
    session.permanent = True
    session["uid"] = uid


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not current_user():
            flash("Avval tizimga kiring.", "warn")
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrap


def admin_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        u = current_user()
        if not u or not u["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrap


def allowed_file(fname):
    return "." in fname and fname.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def save_upload(file_storage):
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_file(file_storage.filename):
        return None
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    name = secrets.token_hex(16) + "." + ext
    file_storage.save(os.path.join(UPLOAD_DIR, name))
    return url_for("uploaded_file", filename=name)


def pdf_to_page_urls(pdf_storage, zoom=2.0):
    """PDF faylni sahifalarga (PNG) bo'lib, URL ro'yxatini qaytaradi."""
    if not pdf_storage or pdf_storage.filename == "":
        return [], None
    if not pdf_storage.filename.lower().endswith(".pdf"):
        return None, "Iltimos .pdf fayl yuklang."
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None, ("PyMuPDF o'rnatilmagan. requirements.txt ga 'PyMuPDF' "
                      "qo'shib, qayta deploy qiling.")
    tmp = os.path.join(UPLOAD_DIR, "tmp_" + secrets.token_hex(8) + ".pdf")
    pdf_storage.save(tmp)
    urls = []
    try:
        doc = fitz.open(tmp)
        mat = fitz.Matrix(zoom, zoom)
        for i in range(len(doc)):
            pix = doc.load_page(i).get_pixmap(matrix=mat)
            name = secrets.token_hex(16) + ".png"
            pix.save(os.path.join(UPLOAD_DIR, name))
            urls.append(url_for("uploaded_file", filename=name))
        doc.close()
    except Exception as e:  # noqa
        return None, f"PDF o'qishda xatolik: {e}"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not urls:
        return None, "PDF ichida sahifa topilmadi."
    return urls, None


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ------------------------------------------------------------ SAYT SOZLAMALARI
def get_setting(key, default=""):
    """settings jadvalidan qiymat o'qiydi."""
    row = get_db().execute(
        "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] else default


def set_setting(key, value):
    """settings jadvaliga qiymat yozadi (bo'sh qiymat = o'chirish)."""
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, (value or "").strip()))
    db.commit()


# --------------------------------------------------- MANGA OLAMI AVATARI (SVG)
# Saytning o'z "maskot" avatari — hech kim rasm qo'ymasa shu ko'rinadi.
# Admin /admin/settings orqali o'zgartira oladi.
DEFAULT_AVATAR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">
<defs>
 <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#f5b942"/>
  <stop offset=".55" stop-color="#ff4d6d"/>
  <stop offset="1" stop-color="#8b5cf6"/>
 </linearGradient>
</defs>
<circle cx="48" cy="48" r="48" fill="#161232"/>
<circle cx="48" cy="48" r="45.5" fill="none" stroke="url(#g)" stroke-width="3"/>
<path d="M28 47 L33 25 L44 37 Q48 35 52 37 L63 25 L68 47
         Q72 57 66 66 Q58 76 48 76 Q38 76 30 66 Q24 57 28 47 Z"
      fill="url(#g)"/>
<path d="M34.5 29 L43 38 Q40 40 38 43 Z" fill="#161232" opacity=".55"/>
<path d="M61.5 29 L53 38 Q56 40 58 43 Z" fill="#161232" opacity=".55"/>
<circle cx="40" cy="55" r="3.6" fill="#0b0918"/>
<circle cx="56" cy="55" r="3.6" fill="#0b0918"/>
<circle cx="41.2" cy="53.8" r="1.1" fill="#fff"/>
<circle cx="57.2" cy="53.8" r="1.1" fill="#fff"/>
<path d="M43.5 64 Q48 68.5 52.5 64" stroke="#0b0918" stroke-width="2.6"
      fill="none" stroke-linecap="round"/>
</svg>"""


@app.route("/avatar-default.svg")
def default_avatar():
    """O'rnatilgan Manga Olami avatari."""
    return app.response_class(DEFAULT_AVATAR_SVG, mimetype="image/svg+xml")


def avatar_of(u):
    """Foydalanuvchi avatari: o'zi yuklagani -> admin qo'ygan standart -> sayt avatari."""
    if u is not None and u["avatar"]:
        return u["avatar"]
    return get_setting("default_avatar") or url_for("default_avatar")


@app.context_processor
def inject_globals():
    return dict(
        SITE_NAME=SITE_NAME, COIN_NAME=COIN_NAME,
        TELEGRAM_ADMIN=TELEGRAM_ADMIN, user=current_user(),
        BOT_USERNAME=TELEGRAM_BOT_USERNAME,
        BOT_ENABLED=bool(TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME),
        avatar_of=avatar_of,
        # Bog'lanish uchun adminlar (sozlamalardan; bo'sh bo'lsa koddagi asosiy admin)
        ADMIN1_URL=get_setting("admin1_url") or TELEGRAM_ADMIN,
        ADMIN1_NAME=get_setting("admin1_name") or "Bosh admin",
        ADMIN2_URL=get_setting("admin2_url"),
        ADMIN2_NAME=get_setting("admin2_name") or "2-admin",
        # Ijtimoiy tarmoqlar (admin sozlamalardan qo'yadi, bo'sh bo'lsa ko'rinmaydi)
        IG_URL=get_setting("instagram_url"),
        TG_CHANNEL=get_setting("tg_channel_url"),
    )


# ============================================================ TELEGRAM KIRISH
def normalize_phone(raw):
    """Telefon raqamni 998901234567 ko'rinishiga keltiradi."""
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(d) == 9:                      # 901234567
        d = "998" + d
    if len(d) == 12 and d.startswith("998"):
        return d
    return None


def tg_api(method, payload):
    """Telegram Bot API ga so'rov yuboradi."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _unique_username(base):
    db = get_db()
    base = "".join(ch for ch in (base or "") if ch.isalnum() or ch in " _-'").strip()
    base = (base or "user")[:30]
    uname = base
    i = 1
    while db.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone():
        i += 1
        uname = f"{base} {i}"
    return uname


@app.route(f"/tg/webhook/{TG_SECRET}", methods=["POST"])
def tg_webhook():
    """Telegram botdan keladigan xabarlar (kontakt / start)."""
    upd = request.get_json(silent=True) or {}
    msg = upd.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return jsonify(ok=True)

    contact = msg.get("contact")
    text = (msg.get("text") or "").strip()
    db = get_db()

    if contact and contact.get("phone_number"):
        # Faqat o'zining kontakti qabul qilinadi
        if contact.get("user_id") and contact["user_id"] != (msg.get("from") or {}).get("id"):
            tg_api("sendMessage", {"chat_id": chat_id,
                                   "text": "Iltimos, faqat o'zingizning raqamingizni yuboring."})
            return jsonify(ok=True)
        phone = normalize_phone(contact["phone_number"])
        if phone:
            db.execute(
                "INSERT INTO tg_links (phone, chat_id, created_at) VALUES (?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET chat_id=excluded.chat_id",
                (phone, chat_id, now()))
            db.commit()
            tg_api("sendMessage", {
                "chat_id": chat_id,
                "text": ("✅ Raqamingiz muvaffaqiyatli bog'landi!\n\n"
                         "Endi saytga qaytib, shu raqamni kiriting — "
                         "kirish kodi shu yerga keladi."),
                "reply_markup": {"remove_keyboard": True}})
        else:
            tg_api("sendMessage", {"chat_id": chat_id,
                                   "text": "Raqam formati noto'g'ri ko'rinadi."})
    elif text.startswith("/start"):
        tg_api("sendMessage", {
            "chat_id": chat_id,
            "text": (f"Assalomu alaykum! 👋\n\n{SITE_NAME} saytiga kirish uchun "
                     "pastdagi tugma orqali telefon raqamingizni yuboring 👇"),
            "reply_markup": {
                "keyboard": [[{"text": "📱 Raqamni yuborish", "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True}})
    return jsonify(ok=True)


@app.route("/tg/set-webhook")
@admin_required
def tg_set_webhook():
    """Bir marta ochiladi — Telegram webhookni ulaydi."""
    if not TELEGRAM_BOT_TOKEN:
        flash("TELEGRAM_BOT_TOKEN sozlanmagan (Railway Variables).", "err")
        return redirect(url_for("admin"))
    base = request.url_root.rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    hook = f"{base}/tg/webhook/{TG_SECRET}"
    res = tg_api("setWebhook", {"url": hook})
    if res and res.get("ok"):
        flash("✓ Telegram bot muvaffaqiyatli ulandi.", "ok")
    else:
        flash(f"Webhook o'rnatishda xatolik: {res}", "err")
    return redirect(url_for("admin"))


def send_login_code(phone):
    """Kod yaratadi va Telegramga yuboradi. (ok, error) qaytaradi."""
    db = get_db()
    link = db.execute("SELECT * FROM tg_links WHERE phone=?", (phone,)).fetchone()
    if not link:
        return False, "not_linked"
    code = f"{secrets.randbelow(900000) + 100000}"
    expires = (datetime.now() + timedelta(minutes=CODE_TTL_MINUTES)) \
        .strftime("%Y-%m-%d %H:%M:%S")
    db.execute("DELETE FROM login_codes WHERE phone=?", (phone,))
    db.execute("INSERT INTO login_codes (phone, code, expires_at) VALUES (?,?,?)",
               (phone, code, expires))
    db.commit()
    res = tg_api("sendMessage", {
        "chat_id": link["chat_id"],
        "text": (f"🔐 {SITE_NAME} — kirish kodingiz:\n\n"
                 f"{code}\n\n"
                 f"Kod {CODE_TTL_MINUTES} daqiqa amal qiladi. "
                 "Uni hech kimga bermang!")})
    if not res or not res.get("ok"):
        return False, "send_failed"
    return True, None


# ----------------------------------------------------------------- AUTH
@app.route("/register")
def register():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))

    show_bot_help = False
    existing_acc = False
    phone_val = ""

    if request.method == "POST":
        form_type = request.form.get("form", "phone")

        # --- Zaxira admin (login/parol) ---
        if form_type == "admin":
            username = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            u = get_db().execute(
                "SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if u and u["password"] and check_password_hash(u["password"], pw):
                do_login(u["id"])
                flash("Tizimga kirdingiz.", "ok")
                nxt = request.args.get("next")
                return redirect(nxt or url_for("index"))
            flash("Login yoki parol xato.", "err")

        # --- Telefon raqam bilan kirish ---
        else:
            phone_val = request.form.get("phone", "").strip()
            phone = normalize_phone(phone_val)
            if not phone:
                flash("Telefon raqamni to'g'ri kiriting. Masalan: 90 123 45 67", "err")
            elif not TELEGRAM_BOT_TOKEN:
                flash("Telegram bot hali sozlanmagan. Admin bilan bog'laning.", "err")
            else:
                ok, err = send_login_code(phone)
                if ok:
                    session["pending_phone"] = phone
                    nxt = request.args.get("next")
                    return redirect(url_for("verify", next=nxt) if nxt
                                    else url_for("verify"))
                if err == "not_linked":
                    show_bot_help = True
                    # Bu raqamga akkaunt bormi? Bo'lsa — qayta ro'yxat SHART EMAS
                    existing_acc = get_db().execute(
                        "SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone() is not None
                    if existing_acc:
                        flash("Bu raqamga akkaunt allaqachon ochilgan ✓ Qaytadan "
                              "ro'yxatdan o'tish shart emas — kod olish uchun "
                              "botga raqamingizni ulang.", "warn")
                    else:
                        flash("Bu raqam hali botga ulanmagan. Avval 1-qadamni bajaring.", "warn")
                else:
                    flash("Kod yuborishda xatolik. Botga /start yozib, qayta urining.", "err")

    tpl = """
    <div class="form-wrap panel fade">
      <div style="text-align:center;margin-bottom:18px">
        <img src="{{ url_for('default_avatar') }}" alt="" style="width:72px;height:72px;margin:0 auto 10px;display:block">
        <h3 style="margin-bottom:4px">Telefon raqam bilan kirish</h3>
        <p style="color:var(--muted);font-size:.86rem">Parol yo'q — kod Telegramingizga keladi</p>
      </div>

      <div class="chip-note">
        👤 Avval akkaunt ochganmisiz? Xuddi shu raqamni kiriting —
        <strong>o'sha akkauntingizga kirasiz</strong>, qayta ro'yxatdan o'tilmaydi.
      </div>

      {% if BOT_ENABLED %}
      <div class="step-box {{ 'glow' if existing_acc or show_bot_help }}">
        <div class="step-t"><span class="step-n">1</span> Botga ulanish
          <span style="color:var(--muted);font-weight:500;font-size:.82rem">(faqat birinchi marta)</span></div>
        <p style="color:var(--muted);font-size:.9rem;margin-bottom:12px">
          Telegram botimizga kirib, <strong>«📱 Raqamni yuborish»</strong> tugmasini bosing.</p>
        <a href="https://t.me/{{ BOT_USERNAME }}" target="_blank" class="btn btn-tg" style="width:100%">✈ @{{ BOT_USERNAME }} botni ochish</a>
      </div>

      <div class="step-box">
        <div class="step-t"><span class="step-n">2</span> Kod olish</div>
        <p style="color:var(--muted);font-size:.9rem;margin-bottom:6px">
          Telefon raqamingizni kiriting — kirish kodi Telegramingizga boradi.</p>
        <form method="post">
          <input type="hidden" name="form" value="phone">
          <label>Telefon raqam</label>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="padding:12px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:11px;color:var(--muted);white-space:nowrap">+998</span>
            <input name="phone" value="{{ phone_val }}" inputmode="tel" autocomplete="tel"
                   placeholder="90 123 45 67" required style="flex:1">
          </div>
          <button class="btn btn-primary" style="width:100%;margin-top:16px">Kod yuborish</button>
        </form>
      </div>
      {% else %}
        <div style="padding:14px;background:var(--bg2);border:1px dashed var(--gold);border-radius:12px;color:var(--gold-soft);font-size:.9rem">
          Telegram orqali kirish hali sozlanmagan. Admin <code>TELEGRAM_BOT_TOKEN</code> va
          <code>TELEGRAM_BOT_USERNAME</code> ni Railway Variables bo'limiga qo'shishi kerak.
          Vaqtincha pastdagi admin kirishidan foydalaning.
        </div>
      {% endif %}

      <details class="admin-fallback">
        <summary>Admin sifatida parol bilan kirish</summary>
        <form method="post" style="margin-top:12px">
          <input type="hidden" name="form" value="admin">
          <label>Login</label><input name="username" autocomplete="username">
          <label>Parol</label><input type="password" name="password" autocomplete="current-password">
          <button class="btn btn-ghost" style="width:100%;margin-top:16px">Kirish</button>
        </form>
      </details>
    </div>
    """
    return render(tpl, show_bot_help=show_bot_help, existing_acc=existing_acc,
                  phone_val=phone_val, title="Kirish")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    if current_user():
        return redirect(url_for("index"))
    phone = session.get("pending_phone")
    if not phone:
        return redirect(url_for("login"))

    db = get_db()

    if request.method == "POST":
        entered = "".join(ch for ch in request.form.get("code", "") if ch.isdigit())
        row = db.execute(
            "SELECT * FROM login_codes WHERE phone=? ORDER BY id DESC LIMIT 1",
            (phone,)).fetchone()
        if not row or row["expires_at"] < now():
            flash("Kod muddati tugagan. Yangi kod oling.", "err")
            return redirect(url_for("login"))
        if row["attempts"] >= CODE_MAX_ATTEMPTS:
            db.execute("DELETE FROM login_codes WHERE id=?", (row["id"],))
            db.commit()
            flash("Juda ko'p xato urinish. Yangi kod oling.", "err")
            return redirect(url_for("login"))
        if entered != row["code"]:
            db.execute("UPDATE login_codes SET attempts=attempts+1 WHERE id=?",
                       (row["id"],))
            db.commit()
            flash("Kod noto'g'ri. Qayta urinib ko'ring.", "err")
        else:
            # Kod to'g'ri
            db.execute("DELETE FROM login_codes WHERE phone=?", (phone,))
            db.commit()
            u = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            if u:
                # Eski akkaunt — QAYTA RO'YXATDAN O'TILMAYDI, o'sha akkauntga kiradi
                if ADMIN_PHONE and normalize_phone(ADMIN_PHONE) == phone and not u["is_admin"]:
                    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
                    db.commit()
                session.pop("pending_phone", None)
                do_login(u["id"])
                flash(f"Xush kelibsiz, {u['username']}!", "ok")
                nxt = request.args.get("next")
                return redirect(nxt or url_for("index"))
            # Yangi foydalanuvchi — ism-familiya so'raymiz
            session["verified_phone"] = phone
            session.pop("pending_phone", None)
            nxt = request.args.get("next")
            return redirect(url_for("complete_signup", next=nxt) if nxt
                            else url_for("complete_signup"))

    masked = phone[:5] + "•••" + phone[-2:]
    tpl = """
    <div class="form-wrap panel fade">
      <h3>Kodni kiriting</h3>
      <p style="color:var(--muted);font-size:.92rem;margin-bottom:6px">
        <strong>+{{ masked }}</strong> raqamiga bog'langan Telegramga 6 xonali kod yubordik.</p>
      <form method="post">
        <label>Kirish kodi</label>
        <input name="code" inputmode="numeric" autocomplete="one-time-code"
               maxlength="6" placeholder="••••••" required autofocus
               style="text-align:center;font-size:1.4rem;letter-spacing:.4em;font-weight:700">
        <button class="btn btn-primary" style="width:100%;margin-top:18px">Tasdiqlash</button>
      </form>
      {% if BOT_ENABLED %}
      <a href="https://t.me/{{ BOT_USERNAME }}" target="_blank" class="btn btn-tg" style="width:100%;margin-top:12px">✈ Kod kelmadimi? Botni ochish</a>
      {% endif %}
      <a href="{{ url_for('login') }}" style="display:block;margin-top:16px;text-align:center;color:var(--muted);font-size:.88rem">← Boshqa raqam / yangi kod olish</a>
    </div>
    """
    return render(tpl, masked=masked, title="Kodni tasdiqlash")


@app.route("/complete", methods=["GET", "POST"])
def complete_signup():
    if current_user():
        return redirect(url_for("index"))
    phone = session.get("verified_phone")
    if not phone:
        return redirect(url_for("login"))

    if request.method == "POST":
        first = request.form.get("first_name", "").strip()
        last = request.form.get("last_name", "").strip()
        if not first:
            flash("Ismingizni kiriting.", "err")
        else:
            db = get_db()
            # Xavfsizlik: shu orada akkaunt paydo bo'lgan bo'lsa — o'shanga kiramiz
            u = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            if not u:
                full = (first + (" " + last if last else "")).strip()
                username = _unique_username(full)
                is_admin_flag = 1 if (ADMIN_PHONE and
                                      normalize_phone(ADMIN_PHONE) == phone) else 0
                db.execute(
                    "INSERT INTO users (username, password, phone, coins, is_admin, "
                    "created_at) VALUES (?,?,?,?,?,?)",
                    (username, "", phone, 0, is_admin_flag, now()))
                db.commit()
                u = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            session.pop("verified_phone", None)
            do_login(u["id"])
            flash(f"Akkaunt yaratildi. Xush kelibsiz, {u['username']}!", "ok")
            nxt = request.args.get("next")
            return redirect(nxt or url_for("index"))

    tpl = """
    <div class="form-wrap panel fade">
      <h3>Oxirgi qadam 🎉</h3>
      <p style="color:var(--muted);font-size:.92rem;margin-bottom:6px">
        Raqamingiz tasdiqlandi. Endi ism va familiyangizni kiriting.</p>
      <form method="post">
        <label>Ism *</label><input name="first_name" required autofocus autocomplete="given-name">
        <label>Familiya</label><input name="last_name" autocomplete="family-name">
        <button class="btn btn-primary" style="width:100%;margin-top:18px">Akkaunt ochish</button>
      </form>
    </div>
    """
    return render(tpl, title="Ro'yxatdan o'tish")


@app.route("/logout")
def logout():
    session.clear()
    flash("Tizimdan chiqdingiz.", "ok")
    return redirect(url_for("index"))


# ================================================================= DIZAYN / CSS
CSS = """
:root{
  --bg:#0b0918; --bg2:#100d22; --surface:#161232; --surface2:#1d1840;
  --border:#2c2658; --border2:#3a3370; --text:#eceaff; --muted:#9a94c4;
  --gold:#f5b942; --gold-soft:#ffcf6b; --pink:#ff4d6d; --violet:#8b5cf6;
  --tg:#2aa9e0;
  --radius:16px; --shadow:0 14px 46px rgba(0,0,0,.5);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  background:
    radial-gradient(1000px 560px at 12% -12%, rgba(139,92,246,.22), transparent 60%),
    radial-gradient(900px 560px at 100% -6%, rgba(245,185,66,.12), transparent 55%),
    radial-gradient(760px 500px at 50% 115%, rgba(255,77,109,.08), transparent 60%),
    var(--bg);
  color:var(--text); font-family:'Inter',system-ui,-apple-system,sans-serif;
  min-height:100vh; line-height:1.55; -webkit-font-smoothing:antialiased;
  overflow-x:hidden; -webkit-tap-highlight-color:transparent;
}
a{color:inherit;text-decoration:none}
img{display:block;max-width:100%}
.container{max-width:1180px;margin:0 auto;padding:0 20px}
::selection{background:rgba(245,185,66,.3)}
::-webkit-scrollbar{width:10px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:10px}

@keyframes fadeUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.fade{animation:fadeUp .5s ease both}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
}

/* ---- Navbar ---- */
.nav{position:sticky;top:0;z-index:60;
  background:rgba(11,9,24,.86);backdrop-filter:blur(16px);
  border-bottom:1px solid var(--border)}
.nav::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,transparent,rgba(245,185,66,.5),rgba(255,77,109,.5),transparent)}
.nav-in{display:flex;align-items:center;gap:18px;height:66px}
.brand{font-family:'Unbounded',sans-serif;font-weight:800;font-size:1.35rem;
  letter-spacing:.5px;white-space:nowrap;
  background:linear-gradient(100deg,var(--gold),var(--pink),var(--violet),var(--gold));
  background-size:260% 100%;-webkit-background-clip:text;background-clip:text;
  color:transparent;animation:brandflow 9s linear infinite}
@keyframes brandflow{to{background-position:260% 0}}
.nav-links{display:flex;gap:6px;flex:1;flex-wrap:wrap}
.nav-links a{padding:8px 14px;border-radius:10px;color:var(--muted);
  font-weight:600;font-size:.95rem;transition:.15s}
.nav-links a:hover{color:var(--text);background:var(--surface)}
.nav-right{display:flex;align-items:center;gap:10px;margin-left:auto}

/* ---- Avatar (gradient halqali) ---- */
.avatar{width:38px;height:38px;border-radius:50%;object-fit:cover;padding:2px;
  background:linear-gradient(135deg,var(--gold),var(--pink) 55%,var(--violet))}
.avatar-xl{width:104px;height:104px;padding:3px;
  box-shadow:0 10px 34px rgba(255,77,109,.25)}
.avatar-form input[type=file]{margin-top:0}

.coin-pill{display:flex;align-items:center;gap:7px;padding:7px 14px;
  border-radius:999px;background:linear-gradient(120deg,#3a2f10,#2a2450);
  border:1px solid var(--gold);font-weight:700;color:var(--gold-soft)}
.coin-pill .dot{width:16px;height:16px;border-radius:50%;
  background:radial-gradient(circle at 35% 30%,#ffe6a0,var(--gold));
  box-shadow:0 0 10px rgba(245,185,66,.6)}

.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:9px 18px;border-radius:11px;font-weight:700;font-size:.95rem;cursor:pointer;
  border:1px solid transparent;transition:.15s;white-space:nowrap}
.btn-primary{background:linear-gradient(120deg,var(--gold),var(--pink));color:#1a1030;
  box-shadow:0 4px 18px rgba(255,77,109,.2)}
.btn-primary:hover{filter:brightness(1.08);transform:translateY(-1px);
  box-shadow:0 8px 24px rgba(255,77,109,.32)}
.btn-danger{background:linear-gradient(120deg,var(--pink),#ff1a40);color:#fff}
.btn-danger:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-ghost{background:var(--surface);border-color:var(--border);color:var(--text)}
.btn-ghost:hover{background:var(--surface2);border-color:var(--border2)}
.btn-tg{background:linear-gradient(120deg,#2aa9e0,#1c7fc4);color:#fff}
.btn-tg:hover{filter:brightness(1.08)}
.btn-ig{background:linear-gradient(45deg,#f9ce34,#ee2a7b 55%,#6228d7);color:#fff}
.btn-ig:hover{filter:brightness(1.08)}

/* ---- Ijtimoiy tarmoq chiplar ---- */
.socials{display:flex;gap:10px;flex-wrap:wrap}
.soc{display:inline-flex;align-items:center;gap:9px;padding:10px 16px;
  border-radius:999px;font-weight:700;font-size:.9rem;color:#fff;transition:.18s;
  box-shadow:0 6px 20px rgba(0,0,0,.35)}
.soc:hover{transform:translateY(-2px) scale(1.02);filter:brightness(1.08)}
.soc .si{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;
  background:rgba(255,255,255,.22);font-size:.85rem}
.soc.tg{background:linear-gradient(120deg,#2aa9e0,#1c7fc4)}
.soc.ig{background:linear-gradient(45deg,#f9ce34,#ee2a7b 55%,#6228d7)}

/* ---- Admin bilan bog'lanish kartalari ---- */
.admin-cards{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
.admin-card{display:flex;align-items:center;gap:13px;padding:15px 16px;
  border-radius:14px;background:var(--bg2);border:1px solid var(--border);transition:.18s}
.admin-card:hover{border-color:var(--tg);transform:translateY(-2px);
  box-shadow:0 8px 26px rgba(42,169,224,.18)}
.admin-card .ic{width:44px;height:44px;border-radius:50%;flex-shrink:0;
  display:grid;place-items:center;font-size:1.25rem;color:#fff;
  background:linear-gradient(135deg,#2aa9e0,#1c7fc4)}
.admin-card b{display:block;font-size:.98rem}
.admin-card span{color:var(--muted);font-size:.8rem}

/* ---- Kirish qadamlari ---- */
.step-box{margin:14px 0;padding:16px;background:var(--bg2);
  border:1px solid var(--border);border-radius:14px}
.step-box.glow{border-color:var(--gold);
  box-shadow:0 0 0 3px rgba(245,185,66,.12)}
.step-t{display:flex;align-items:center;gap:9px;font-weight:700;margin-bottom:8px}
.step-n{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;
  font-size:.8rem;font-weight:800;color:#1a1030;
  background:linear-gradient(120deg,var(--gold),var(--pink))}
.chip-note{padding:12px 14px;margin-bottom:6px;border-radius:12px;font-size:.85rem;
  color:var(--muted);background:rgba(139,92,246,.08);
  border:1px solid rgba(139,92,246,.35)}
.chip-note strong{color:var(--gold-soft)}

/* ---- Hero ---- */
.hero{position:relative;padding:64px 0 40px;overflow:hidden}
.hero::before{content:"";position:absolute;inset:-40% 30% auto auto;width:420px;height:420px;
  background:radial-gradient(circle,rgba(139,92,246,.28),transparent 70%);filter:blur(8px);
  animation:glow 8s ease-in-out infinite alternate;pointer-events:none}
@keyframes glow{from{transform:translate(0,0)}to{transform:translate(-40px,30px)}}
.hero h1{font-family:'Sora',sans-serif;font-size:clamp(2.1rem,5.4vw,3.5rem);
  font-weight:800;line-height:1.05;max-width:740px;letter-spacing:-.5px}
.hero .hl{background:linear-gradient(100deg,var(--gold),var(--pink));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{color:var(--muted);margin-top:16px;font-size:1.08rem;max-width:560px}
.hero .cta{display:flex;gap:12px;margin-top:26px;flex-wrap:wrap}
.hero-stats{display:flex;gap:26px;margin-top:30px;flex-wrap:wrap}
.hero-stats .s .n{font-family:'Sora';font-size:1.6rem;font-weight:800;color:var(--gold)}
.hero-stats .s .l{color:var(--muted);font-size:.85rem}

/* ---- Sections ---- */
.section{padding:34px 0}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;
  margin-bottom:20px;gap:14px;flex-wrap:wrap}
.sec-head h2{font-family:'Sora',sans-serif;font-size:1.5rem;font-weight:700}
.sec-head .eyebrow{color:var(--gold);font-weight:700;font-size:.8rem;
  letter-spacing:.14em;text-transform:uppercase;display:inline-flex;align-items:center;
  gap:8px;margin-bottom:6px}
.sec-head .eyebrow::before{content:"";width:20px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,var(--gold),var(--pink))}
.sec-head a{color:var(--muted);font-weight:600;font-size:.9rem}
.sec-head a:hover{color:var(--gold)}

/* ---- Manga grid ---- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:20px}
.card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);overflow:hidden;transition:.2s;position:relative}
.card:hover{transform:translateY(-5px);border-color:var(--violet);box-shadow:var(--shadow)}
.card .cover-wrap{position:relative;overflow:hidden}
.card .cover-wrap::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(115deg,transparent 42%,rgba(255,255,255,.13) 50%,transparent 58%);
  transform:translateX(-130%);transition:transform .55s ease}
.card:hover .cover-wrap::after{transform:translateX(130%)}
.card .cover{aspect-ratio:5/7;width:100%;object-fit:cover;background:var(--surface2);
  transition:transform .45s ease}
.card:hover .cover{transform:scale(1.06)}
.card .body{padding:12px 13px 14px}
.card .title{font-weight:700;font-size:.98rem;line-height:1.25;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .genres{color:var(--muted);font-size:.78rem;margin-top:5px}
.card .rating{position:absolute;top:10px;left:10px;padding:4px 9px;border-radius:8px;
  background:rgba(11,9,24,.82);border:1px solid var(--gold);color:var(--gold-soft);
  font-weight:700;font-size:.8rem;backdrop-filter:blur(4px);z-index:2}
.badge-prem{position:absolute;top:10px;right:10px;padding:3px 8px;border-radius:8px;
  background:linear-gradient(120deg,var(--gold),var(--pink));color:#1a1030;
  font-weight:800;font-size:.68rem;z-index:2}

/* ---- List ---- */
.rows{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.row{display:flex;gap:13px;padding:12px;background:var(--surface);
  border:1px solid var(--border);border-radius:14px;transition:.15s}
.row:hover{border-color:var(--violet);transform:translateY(-2px)}
.row img{width:56px;height:76px;object-fit:cover;border-radius:9px;flex-shrink:0}
.row .meta{min-width:0}
.row .meta .t{font-weight:700;font-size:.95rem;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.row .meta .g{color:var(--muted);font-size:.78rem;margin-top:3px}
.row .meta .c{color:var(--gold);font-size:.82rem;font-weight:600;margin-top:6px}

/* ---- Panels / forms (nozik gradient hoshiya) ---- */
.panel{border-radius:var(--radius);padding:26px;border:1px solid transparent;
  background:
    linear-gradient(var(--surface),var(--surface)) padding-box,
    linear-gradient(155deg,rgba(245,185,66,.32),rgba(139,92,246,.22) 45%,var(--border) 80%) border-box}
.panel h3{font-family:'Sora',sans-serif;font-size:1.2rem;margin-bottom:16px}
label{display:block;font-weight:600;font-size:.88rem;margin:14px 0 6px;color:var(--muted)}
input,textarea,select{width:100%;padding:12px 14px;border-radius:11px;
  background:var(--bg2);border:1px solid var(--border);color:var(--text);
  font-size:.98rem;font-family:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--violet);
  box-shadow:0 0 0 3px rgba(139,92,246,.18)}
textarea{min-height:100px;resize:vertical}
.row-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.form-wrap{max-width:440px;margin:48px auto}
.check{display:flex;align-items:center;gap:10px;margin-top:14px}
.check input{width:auto}
.hint{color:var(--muted);font-size:.82rem;margin-top:6px}
.divider{display:flex;align-items:center;gap:12px;color:var(--muted);
  font-size:.82rem;margin:22px 0}
.divider::before,.divider::after{content:"";flex:1;height:1px;background:var(--border)}
details.admin-fallback{margin-top:22px;border-top:1px solid var(--border);padding-top:14px}
details.admin-fallback summary{cursor:pointer;color:var(--muted);font-size:.86rem;
  font-weight:600;list-style:none}
details.admin-fallback summary::-webkit-details-marker{display:none}

/* ---- Reader ---- */
.reader{max-width:820px;margin:0 auto;padding:20px}
.reader img{width:100%;border-radius:6px;margin-bottom:4px;background:var(--surface2)}
.reader-bar{display:flex;justify-content:space-between;align-items:center;
  gap:12px;padding:16px 0;flex-wrap:wrap}
.locked{max-width:520px;margin:60px auto;text-align:center}
.locked .big{font-size:3rem;margin-bottom:10px}

/* ---- Detail ---- */
.detail-top{display:grid;grid-template-columns:230px 1fr;gap:30px;margin-top:30px}
.detail-top .cover{width:230px;aspect-ratio:5/7;object-fit:cover;
  border-radius:var(--radius);border:1px solid var(--border)}
.detail-top h1{font-family:'Sora',sans-serif;font-size:2rem;font-weight:800}
.tags{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
.tag{padding:5px 12px;border-radius:999px;background:var(--surface2);
  border:1px solid var(--border);font-size:.82rem;color:var(--muted)}
.chapter-list{margin-top:10px;display:flex;flex-direction:column;gap:8px}
.chapter{display:flex;justify-content:space-between;align-items:center;
  padding:14px 16px;background:var(--surface);border:1px solid var(--border);
  border-radius:12px;transition:.15s}
.chapter:hover{border-color:var(--violet);background:var(--surface2);transform:translateX(3px)}
.chapter .cprice{color:var(--gold);font-weight:700;font-size:.86rem}
.free{color:#8ef0b0;font-weight:600;font-size:.86rem}

/* ---- Flash ---- */
.flashes{position:fixed;top:78px;right:20px;z-index:100;display:flex;
  flex-direction:column;gap:10px;max-width:340px}
.flash{padding:13px 16px;border-radius:12px;font-weight:600;font-size:.9rem;
  box-shadow:var(--shadow);animation:slidein .3s}
.flash.ok{background:#132a1c;border:1px solid #2f7d4f;color:#8ef0b0}
.flash.err{background:#2a1320;border:1px solid #a03a55;color:#ff9db3}
.flash.warn{background:#2a2410;border:1px solid #8a6d1e;color:var(--gold-soft)}
@keyframes slidein{from{transform:translateX(30px);opacity:0}}

/* ---- Table ---- */
table{width:100%;border-collapse:collapse;margin-top:12px}
th,td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--border);font-size:.9rem}
th{color:var(--muted);font-weight:600;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em}

/* ---- Footer ---- */
footer{border-top:1px solid var(--border);margin-top:50px;padding:36px 0;color:var(--muted)}
.foot-in{display:flex;justify-content:space-between;gap:30px;flex-wrap:wrap}
.foot-in .brand{font-size:1.1rem}
.foot-links a{display:block;color:var(--muted);padding:4px 0;font-size:.9rem}
.foot-links a:hover{color:var(--gold)}

.empty{text-align:center;color:var(--muted);padding:60px 20px}
.pageid{font-family:'Sora',monospace;background:var(--bg2);border:1px dashed var(--gold);
  padding:6px 12px;border-radius:9px;color:var(--gold-soft);font-weight:700}

/* ---- Floating Telegram ---- */
.fab{position:fixed;right:18px;bottom:18px;z-index:70;display:flex;align-items:center;
  gap:9px;padding:12px 16px;border-radius:999px;font-weight:700;color:#fff;
  background:linear-gradient(120deg,#2aa9e0,#1c7fc4);box-shadow:0 10px 30px rgba(28,127,196,.45);
  transition:.18s}
.fab:hover{transform:translateY(-2px) scale(1.03)}

/* ---- Pastki tab-menyu (faqat telefon) ---- */
.bottomnav{display:none}

@media(max-width:820px){
  .nav-in{height:58px;gap:10px}
  .nav-links{display:none}
  .nav-logout{display:none}
  .brand{font-size:1.15rem}
  .coin-pill{padding:6px 11px}
  .avatar{width:34px;height:34px}
  .hero{padding:38px 0 24px}
  .hero p{font-size:1rem;margin-top:12px}
  .hero .cta{margin-top:20px}
  .hero-stats{gap:20px;margin-top:24px}
  .section{padding:24px 0}
  .sec-head{margin-bottom:16px}

  /* iOS zoom oldini olish uchun 16px */
  input,select,textarea{font-size:16px}

  .flashes{top:64px;right:10px;left:10px;max-width:none}
  .form-wrap{margin:24px auto}
  .reader{padding:10px 8px}
  .reader-bar{padding:12px 0}
  .reader-bar .btn{flex:1}
  .admin-cards{grid-template-columns:1fr}

  /* Suzuvchi Telegram tugmasi — pastki menyu tepasiga, ixcham */
  .fab{padding:13px;font-size:0;right:14px;
    bottom:calc(74px + env(safe-area-inset-bottom))}
  .fab::before{content:"✈";font-size:1.25rem}

  /* Pastki navigatsiya */
  .bottomnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:80;
    background:rgba(11,9,24,.97);backdrop-filter:blur(18px);
    border-top:1px solid var(--border);justify-content:space-around;
    padding:8px 6px calc(8px + env(safe-area-inset-bottom))}
  .bottomnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;
    color:var(--muted);font-size:.68rem;font-weight:600;padding:5px 2px;border-radius:12px;
    transition:.15s}
  .bottomnav a .i{font-size:1.3rem;line-height:1}
  .bottomnav a:active{background:var(--surface)}
  .bottomnav a.on{color:var(--gold)}
  .bottomnav a.on .i{filter:drop-shadow(0 0 8px rgba(245,185,66,.55))}

  /* Kontent pastki menyu ostida qolib ketmasligi uchun */
  body{padding-bottom:calc(66px + env(safe-area-inset-bottom))}

  /* ======== TELEFON = MOBIL ILOVA KO'RINISHI ======== */

  /* Footer telefonda ko'rinmaydi — ilovaga o'xshash */
  footer{display:none}

  /* Yuqori panel — ixcham ilova sarlavhasi */
  .nav{background:rgba(11,9,24,.94)}
  .nav-right .btn-ghost{padding:7px 12px;font-size:.85rem}

  /* Mashhur asarlar — gorizontal surish (app-style carousel) */
  .h-scroll{display:flex;overflow-x:auto;gap:12px;
    scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch;
    margin:0 -20px;padding:2px 20px 8px;scrollbar-width:none}
  .h-scroll::-webkit-scrollbar{display:none}
  .h-scroll .card{flex:0 0 150px;min-width:150px;scroll-snap-align:start}
  .h-scroll .card .title{font-size:.86rem}

  /* Hero — ixcham banner */
  .hero h1{font-size:1.75rem}
  .hero-stats .s .n{font-size:1.25rem}
  .hero .cta .btn{flex:1;padding:11px 14px}

  /* Manga sahifasi — markazlashgan app ko'rinishi */
  .detail-top{gap:18px;margin-top:18px}
  .detail-top > div:first-child{display:flex;flex-direction:column;align-items:center}
  .detail-top h1{text-align:center}
  .detail-top .tags{justify-content:center}
  .detail-top p{text-align:center;margin:0 auto}

  /* O'quvchi — chetdan-chetga to'liq ekran */
  .reader{padding:0}
  .reader img{border-radius:0;margin-bottom:0}
  .reader-bar{padding:12px 10px}

  /* Pastki menyu — kattaroq, faol tugma "pill" bilan */
  .bottomnav a{padding:7px 4px}
  .bottomnav a .i{font-size:1.45rem}
  .bottomnav a.on{background:rgba(245,185,66,.1);color:var(--gold)}

  /* Kartochkalar telefonda yumshoqroq */
  .card{border-radius:14px}
  .card:hover{transform:none}
  .panel{border-radius:14px}
}

/* ======== NOUTBUK / PC = TO'LIQ VEB-SAYT KO'RINISHI ======== */
@media(min-width:821px){
  .bottomnav{display:none !important}
  .grid{grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:24px}
  .h-scroll{display:grid}
  .container{padding:0 32px}
  .hero{padding:80px 0 50px}
  .section{padding:44px 0}
  .nav-links a{position:relative}
  .nav-links a::after{content:"";position:absolute;left:14px;right:14px;bottom:4px;
    height:2px;border-radius:2px;background:linear-gradient(90deg,var(--gold),var(--pink));
    transform:scaleX(0);transition:transform .2s;transform-origin:left}
  .nav-links a:hover::after{transform:scaleX(1)}
  .reader{padding:28px 20px}
  .fab{padding:12px 18px;font-size:.95rem}
}

@media(max-width:720px){
  .rows{grid-template-columns:1fr}
  .detail-top{grid-template-columns:1fr}
  .detail-top .cover{width:100%;max-width:220px}
  .detail-top h1{font-size:1.55rem}
  .row-2{grid-template-columns:1fr}
  .grid{grid-template-columns:repeat(2,1fr);gap:12px}
  .panel{padding:18px}
  .sec-head h2{font-size:1.3rem}
  .card .title{font-size:.9rem}
}

@media(max-width:380px){
  .brand{font-size:1.05rem}
  .coin-pill{padding:5px 9px;font-size:.85rem}
  .bottomnav a{font-size:.62rem}
  .bottomnav a .i{font-size:1.2rem}
}
"""

# ================================================================ ASOSIY SHABLON
BASE = """
<!doctype html><html lang="uz"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0918">
<title>{{ title or SITE_NAME }}</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('default_avatar') }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Sora:wght@600;700;800&family=Unbounded:wght@700;800&display=swap" rel="stylesheet">
<style>{{ css|safe }}</style>
</head><body>

<nav class="nav"><div class="container nav-in">
  <a href="{{ url_for('index') }}" class="brand">◈ {{ SITE_NAME }}</a>
  <div class="nav-links" id="navlinks">
    <a href="{{ url_for('index') }}">Bosh sahifa</a>
    <a href="{{ url_for('catalog') }}">Katalog</a>
    {% if user %}<a href="{{ url_for('bookmarks') }}">Saqlanganlar</a>{% endif %}
    <a href="{{ url_for('coins') }}">Tanga olish</a>
    {% if user and user['is_admin'] %}<a href="{{ url_for('admin') }}">Admin panel</a>{% endif %}
  </div>
  <div class="nav-right">
    {% if user %}
      <a href="{{ url_for('coins') }}" class="coin-pill"><span class="dot"></span>{{ user['coins'] }}</a>
      <a href="{{ url_for('profile') }}" title="{{ user['username'] }}">
        <img class="avatar" src="{{ avatar_of(user) }}" alt="{{ user['username'] }}">
      </a>
      <a href="{{ url_for('logout') }}" class="btn btn-ghost nav-logout">Chiqish</a>
    {% else %}
      <a href="{{ url_for('login') }}" class="btn btn-primary">Kirish</a>
    {% endif %}
  </div>
</div></nav>

{% with msgs = get_flashed_messages(with_categories=true) %}
{% if msgs %}<div class="flashes">
  {% for cat,m in msgs %}
    <div class="flash {{ 'ok' if cat=='ok' else 'err' if cat=='err' else 'warn' }}">{{ m }}</div>
  {% endfor %}
</div>{% endif %}
{% endwith %}

<main>{{ body|safe }}</main>

<a href="{{ ADMIN1_URL }}" target="_blank" class="fab" title="Telegram admin">✈ Admin</a>

<nav class="bottomnav">
  <a href="{{ url_for('index') }}" class="{{ 'on' if request.path == '/' }}"><span class="i">🏠</span>Bosh</a>
  <a href="{{ url_for('catalog') }}" class="{{ 'on' if '/catalog' in request.path or '/manga' in request.path or '/read' in request.path }}"><span class="i">🔍</span>Katalog</a>
  {% if user %}<a href="{{ url_for('bookmarks') }}" class="{{ 'on' if '/bookmark' in request.path }}"><span class="i">☆</span>Saqlangan</a>{% endif %}
  <a href="{{ url_for('coins') }}" class="{{ 'on' if '/coins' in request.path }}"><span class="i">◉</span>Tanga</a>
  {% if user %}<a href="{{ url_for('profile') }}" class="{{ 'on' if '/profile' in request.path or '/admin' in request.path }}"><span class="i">👤</span>Profil</a>
  {% else %}<a href="{{ url_for('login') }}" class="{{ 'on' if '/login' in request.path or '/verify' in request.path or '/complete' in request.path }}"><span class="i">👤</span>Kirish</a>{% endif %}
</nav>

<footer><div class="container foot-in">
  <div style="max-width:340px">
    <div class="brand">◈ {{ SITE_NAME }}</div>
    <p style="margin-top:12px;font-size:.92rem">Eng sara manga, manhwa va manhualarni o'zbek tilida sifatli tarjimada o'qing.</p>
    <div class="socials" style="margin-top:16px">
      {% if TG_CHANNEL %}<a href="{{ TG_CHANNEL }}" target="_blank" class="soc tg"><span class="si">✈</span>Kanal</a>{% endif %}
      {% if IG_URL %}<a href="{{ IG_URL }}" target="_blank" class="soc ig"><span class="si">📷</span>Instagram</a>{% endif %}
    </div>
  </div>
  <div class="foot-links">
    <strong style="color:var(--text)">Navigatsiya</strong>
    <a href="{{ url_for('index') }}">Bosh sahifa</a>
    <a href="{{ url_for('catalog') }}">Katalog</a>
    <a href="{{ url_for('coins') }}">Tanga sotib olish</a>
  </div>
  <div class="foot-links">
    <strong style="color:var(--text)">Aloqa</strong>
    <a href="{{ ADMIN1_URL }}" target="_blank">✈ {{ ADMIN1_NAME }}</a>
    {% if ADMIN2_URL %}<a href="{{ ADMIN2_URL }}" target="_blank">✈ {{ ADMIN2_NAME }}</a>{% endif %}
    {% if TG_CHANNEL %}<a href="{{ TG_CHANNEL }}" target="_blank">📢 Telegram kanal</a>{% endif %}
    {% if IG_URL %}<a href="{{ IG_URL }}" target="_blank">📷 Instagram</a>{% endif %}
  </div>
</div>
<div class="container" style="margin-top:24px;font-size:.85rem">© 2026 {{ SITE_NAME }}. Barcha huquqlar himoyalangan.</div>
</footer>
</body></html>
"""


def page(body, title=None):
    return render_template_string(BASE, body=body, css=CSS, title=title)


def render(tpl, **kw):
    body = render_template_string(tpl, **kw)
    return page(body, kw.get("title"))


# ===================================================================== SAHIFALAR
@app.route("/")
def index():
    db = get_db()
    popular = db.execute(
        "SELECT * FROM manga ORDER BY rating DESC LIMIT 12").fetchall()
    latest = db.execute("""
        SELECT m.*, (SELECT MAX(number) FROM chapters c WHERE c.manga_id=m.id) AS last_ch
        FROM manga m ORDER BY m.created_at DESC, m.id DESC LIMIT 8""").fetchall()
    total_manga = db.execute("SELECT COUNT(*) FROM manga").fetchone()[0]
    total_chapters = db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]

    tpl = """
    <section class="hero"><div class="container fade">
      <h1>Manga <span class="hl">olamiga</span> xush kelibsiz</h1>
      <p>Minglab boblar, sara tarjimalar va yangi chiqqan manhwalar — hammasi bir joyda, o'zbek tilida.</p>
      <div class="cta">
        <a href="{{ url_for('catalog') }}" class="btn btn-primary">Katalogni ko'rish</a>
        <a href="{{ url_for('coins') }}" class="btn btn-ghost"><span style="color:var(--gold)">◉</span> Tanga sotib olish</a>
      </div>
      <div class="hero-stats">
        <div class="s"><div class="n">{{ total_manga }}</div><div class="l">Asar</div></div>
        <div class="s"><div class="n">{{ total_chapters }}</div><div class="l">Bob</div></div>
        <div class="s"><div class="n">Uz</div><div class="l">Tarjima</div></div>
      </div>
    </div></section>

    <section class="section"><div class="container">
      <div class="sec-head">
        <div><span class="eyebrow">Trend</span><h2>Mashhur asarlar</h2></div>
        <a href="{{ url_for('catalog') }}?sort=rating">Barchasi →</a>
      </div>
      {% if popular %}
      <div class="grid h-scroll">
        {% for m in popular %}{{ card(m)|safe }}{% endfor %}
      </div>
      {% else %}<div class="empty">Hali manga qo'shilmagan.</div>{% endif %}
    </div></section>

    <section class="section"><div class="container">
      <div class="sec-head"><div><span class="eyebrow">Yangi</span><h2>So'nggi yangilanishlar</h2></div></div>
      <div class="rows">
        {% for m in latest %}
        <a href="{{ url_for('manga_detail', slug=m['slug']) }}" class="row">
          <img src="{{ m['cover'] }}" alt="">
          <div class="meta">
            <div class="t">{{ m['title'] }}</div>
            <div class="g">{{ m['genres'] }}</div>
            <div class="c">{% if m['last_ch'] %}{{ m['last_ch']|int }}-bob{% else %}Tez orada{% endif %}</div>
          </div>
        </a>
        {% endfor %}
      </div>
    </div></section>
    """
    return render(tpl, popular=popular, latest=latest,
                  total_manga=total_manga, total_chapters=total_chapters,
                  card=card_macro, title=SITE_NAME)


def card_macro(m):
    prem = get_db().execute(
        "SELECT 1 FROM chapters WHERE manga_id=? AND is_premium=1 LIMIT 1",
        (m["id"],)).fetchone()
    return render_template_string("""
    <a href="{{ url_for('manga_detail', slug=m['slug']) }}" class="card">
      <div class="cover-wrap">
        <img class="cover" src="{{ m['cover'] }}" alt="{{ m['title'] }}" loading="lazy">
        <span class="rating">★ {{ '%.1f'|format(m['rating']) }}</span>
        {% if prem %}<span class="badge-prem">PREMIUM</span>{% endif %}
      </div>
      <div class="body">
        <div class="title">{{ m['title'] }}</div>
        <div class="genres">{{ m['genres'] }}</div>
      </div>
    </a>
    """, m=m, prem=prem)


@app.route("/catalog")
def catalog():
    db = get_db()
    q = request.args.get("q", "").strip()
    genre = request.args.get("genre", "").strip()
    sort = request.args.get("sort", "new")

    sql = "SELECT * FROM manga WHERE 1=1"
    params = []
    if q:
        sql += " AND title LIKE ?"
        params.append(f"%{q}%")
    if genre:
        sql += " AND genres LIKE ?"
        params.append(f"%{genre}%")
    sql += " ORDER BY rating DESC" if sort == "rating" else " ORDER BY id DESC"
    items = db.execute(sql, params).fetchall()

    all_genres = ["Jangari", "Romantika", "Fantaziya", "Sarguzasht",
                  "Drama", "Komediya", "Isekai", "Maktab hayoti"]

    tpl = """
    <section class="section"><div class="container">
      <div class="sec-head"><div><span class="eyebrow">Katalog</span>
        <h2>Barcha asarlar {% if items %}<span style="color:var(--muted);font-size:1rem">({{ items|length }})</span>{% endif %}</h2></div>
      </div>

      <form method="get" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:24px">
        <input name="q" value="{{ q }}" placeholder="Manga nomi bo'yicha qidirish..." style="flex:1;min-width:220px">
        <select name="genre" style="max-width:200px">
          <option value="">Barcha janrlar</option>
          {% for g in all_genres %}<option value="{{ g }}" {{ 'selected' if g==genre }}>{{ g }}</option>{% endfor %}
        </select>
        <select name="sort" style="max-width:170px">
          <option value="new" {{ 'selected' if sort=='new' }}>Yangi</option>
          <option value="rating" {{ 'selected' if sort=='rating' }}>Reyting</option>
        </select>
        <button class="btn btn-primary">Qidirish</button>
      </form>

      {% if items %}
      <div class="grid">{% for m in items %}{{ card(m)|safe }}{% endfor %}</div>
      {% else %}<div class="empty">Hech narsa topilmadi.</div>{% endif %}
    </div></section>
    """
    return render(tpl, items=items, q=q, genre=genre, sort=sort,
                  all_genres=all_genres, card=card_macro, title="Katalog")


@app.route("/manga/<slug>")
def manga_detail(slug):
    db = get_db()
    m = db.execute("SELECT * FROM manga WHERE slug=?", (slug,)).fetchone()
    if not m:
        abort(404)
    chapters = db.execute(
        "SELECT * FROM chapters WHERE manga_id=? ORDER BY number ASC", (m["id"],)
    ).fetchall()

    u = current_user()
    owned = set()
    bookmarked = False
    if u:
        owned = {r["chapter_id"] for r in db.execute(
            "SELECT chapter_id FROM purchases WHERE user_id=?", (u["id"],)).fetchall()}
        bookmarked = db.execute(
            "SELECT 1 FROM bookmarks WHERE user_id=? AND manga_id=?",
            (u["id"], m["id"])).fetchone() is not None

    tpl = """
    <div class="container detail-top fade">
      <div>
        <img class="cover" src="{{ m['cover'] }}" alt="{{ m['title'] }}">
        {% if user %}
        <form method="post" action="{{ url_for('toggle_bookmark', manga_id=m['id']) }}" style="margin-top:14px">
          <button class="btn {{ 'btn-primary' if not bookmarked else 'btn-ghost' }}" style="width:100%">
            {{ '★ Saqlangan' if bookmarked else '☆ Saqlash' }}
          </button>
        </form>
        {% endif %}
        {% if user and user['is_admin'] %}
        <form method="post" action="{{ url_for('admin_delete_manga', manga_id=m['id']) }}" style="margin-top:10px" onsubmit="return confirm('Haqiqatdan ham ushbu mangani butunlay o‘chirmoqchimisiz?');">
          <button class="btn btn-danger" style="width:100%">🗑 Mangani o'chirish</button>
        </form>
        {% endif %}
      </div>
      <div>
        <h1>{{ m['title'] }}</h1>
        <div class="tags">
          <span class="tag">★ {{ '%.1f'|format(m['rating']) }}</span>
          <span class="tag">{{ m['status'] }}</span>
          {% for g in m['genres'].split(',') if g.strip() %}<span class="tag">{{ g.strip() }}</span>{% endfor %}
        </div>
        <p style="color:var(--muted);max-width:640px">{{ m['description'] }}</p>

        <div class="sec-head" style="margin-top:28px"><h2 style="font-size:1.25rem">Boblar</h2></div>
        {% if chapters %}
        <div class="chapter-list">
          {% for c in chapters %}
          <a href="{{ url_for('read', chapter_id=c['id']) }}" class="chapter">
            <span><strong>{{ c['number']|int if c['number']==c['number']|int else c['number'] }}-bob</strong>
              {% if c['title'] %}<span style="color:var(--muted)"> · {{ c['title'] }}</span>{% endif %}</span>
            {% if c['is_premium'] %}
              {% if c['id'] in owned %}<span class="free">✓ Ochilgan</span>
              {% else %}<span class="cprice">◉ {{ c['coin_cost'] }} tanga</span>{% endif %}
            {% else %}<span class="free">Bepul</span>{% endif %}
          </a>
          {% endfor %}
        </div>
        {% else %}<div class="empty">Hali bob qo'shilmagan.</div>{% endif %}
      </div>
    </div>
    <div style="height:40px"></div>
    """
    return render(tpl, m=m, chapters=chapters, owned=owned,
                  bookmarked=bookmarked, title=m["title"])


@app.route("/read/<int:chapter_id>")
def read(chapter_id):
    db = get_db()
    ch = db.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if not ch:
        abort(404)
    m = db.execute("SELECT * FROM manga WHERE id=?", (ch["manga_id"],)).fetchone()
    u = current_user()

    if ch["is_premium"]:
        if not u:
            flash("Premium bobni o'qish uchun tizimga kiring.", "warn")
            return redirect(url_for("login", next=request.path))
        owned = db.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND chapter_id=?",
            (u["id"], ch["id"])).fetchone()
        if not owned:
            tpl = """
            <div class="container"><div class="locked panel">
              <div class="big">🔒</div>
              <h3>Bu premium bob</h3>
              <p style="color:var(--muted);margin:8px 0 20px">
                "{{ m['title'] }} — {{ ch['number']|int }}-bob"ni ochish uchun
                <strong style="color:var(--gold)">{{ ch['coin_cost'] }} {{ COIN_NAME }}</strong> kerak.<br>
                Sizda hozir: <strong style="color:var(--gold)">{{ user['coins'] }} {{ COIN_NAME }}</strong></p>
              {% if user['coins'] >= ch['coin_cost'] %}
              <form method="post" action="{{ url_for('unlock', chapter_id=ch['id']) }}">
                <button class="btn btn-primary" style="width:100%">◉ {{ ch['coin_cost'] }} tangaga ochish</button>
              </form>
              {% else %}
              <p style="color:var(--pink);margin-bottom:16px">Tangangiz yetarli emas.</p>
              <a href="{{ url_for('coins') }}" class="btn btn-primary" style="width:100%">Tanga sotib olish</a>
              {% endif %}
              <a href="{{ url_for('manga_detail', slug=m['slug']) }}" style="display:block;margin-top:14px;color:var(--muted)">← Ortga</a>
            </div></div>
            """
            return render(tpl, m=m, ch=ch, title="Premium bob")

    pages = db.execute(
        "SELECT * FROM pages WHERE chapter_id=? ORDER BY page_number ASC",
        (chapter_id,)).fetchall()
    prev_ch = db.execute(
        "SELECT id FROM chapters WHERE manga_id=? AND number<? ORDER BY number DESC LIMIT 1",
        (ch["manga_id"], ch["number"])).fetchone()
    next_ch = db.execute(
        "SELECT id FROM chapters WHERE manga_id=? AND number>? ORDER BY number ASC LIMIT 1",
        (ch["manga_id"], ch["number"])).fetchone()

    tpl = """
    <div class="reader">
      <div class="reader-bar">
        <a href="{{ url_for('manga_detail', slug=m['slug']) }}" class="btn btn-ghost">← {{ m['title'] }}</a>
        <strong>{{ ch['number']|int }}-bob</strong>
      </div>
      {% if pages %}
        {% for p in pages %}<img src="{{ p['image'] }}" loading="lazy" alt="{{ p['page_number'] }}">{% endfor %}
      {% else %}<div class="empty">Bu bobda sahifa yo'q.</div>{% endif %}
      <div class="reader-bar">
        {% if prev_ch %}<a href="{{ url_for('read', chapter_id=prev_ch['id']) }}" class="btn btn-ghost">← Oldingi</a>{% else %}<span></span>{% endif %}
        {% if next_ch %}<a href="{{ url_for('read', chapter_id=next_ch['id']) }}" class="btn btn-primary">Keyingi →</a>{% endif %}
      </div>
    </div>
    """
    return render(tpl, m=m, ch=ch, pages=pages, prev_ch=prev_ch,
                  next_ch=next_ch, title=f"{m['title']} {ch['number']}-bob")


@app.route("/unlock/<int:chapter_id>", methods=["POST"])
@login_required
def unlock(chapter_id):
    db = get_db()
    u = current_user()
    ch = db.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if not ch or not ch["is_premium"]:
        abort(404)
    owned = db.execute("SELECT 1 FROM purchases WHERE user_id=? AND chapter_id=?",
                       (u["id"], ch["id"])).fetchone()
    if not owned:
        if u["coins"] < ch["coin_cost"]:
            flash("Tangangiz yetarli emas.", "err")
            return redirect(url_for("coins"))
        db.execute("UPDATE users SET coins=coins-? WHERE id=?", (ch["coin_cost"], u["id"]))
        db.execute("INSERT INTO purchases (user_id, chapter_id, created_at) VALUES (?,?,?)",
                   (u["id"], ch["id"], now()))
        db.commit()
        flash(f"Bob ochildi! {ch['coin_cost']} {COIN_NAME} sarflandi.", "ok")
    return redirect(url_for("read", chapter_id=chapter_id))


# ------------------------------------------------------------- BOOKMARK
@app.route("/bookmark/<int:manga_id>", methods=["POST"])
@login_required
def toggle_bookmark(manga_id):
    db = get_db()
    u = current_user()
    ex = db.execute("SELECT id FROM bookmarks WHERE user_id=? AND manga_id=?",
                    (u["id"], manga_id)).fetchone()
    if ex:
        db.execute("DELETE FROM bookmarks WHERE id=?", (ex["id"],))
    else:
        db.execute("INSERT INTO bookmarks (user_id, manga_id) VALUES (?,?)",
                   (u["id"], manga_id))
    db.commit()
    m = db.execute("SELECT slug FROM manga WHERE id=?", (manga_id,)).fetchone()
    return redirect(url_for("manga_detail", slug=m["slug"]))


@app.route("/bookmarks")
@login_required
def bookmarks():
    db = get_db()
    u = current_user()
    items = db.execute("""
        SELECT m.* FROM manga m JOIN bookmarks b ON b.manga_id=m.id
        WHERE b.user_id=? ORDER BY b.id DESC""", (u["id"],)).fetchall()
    tpl = """
    <section class="section"><div class="container">
      <div class="sec-head"><div><span class="eyebrow">Kutubxona</span><h2>Saqlanganlar</h2></div></div>
      {% if items %}<div class="grid">{% for m in items %}{{ card(m)|safe }}{% endfor %}</div>
      {% else %}<div class="empty">Hali hech narsa saqlamagansiz.</div>{% endif %}
    </div></section>
    """
    return render(tpl, items=items, card=card_macro, title="Saqlanganlar")


# ------------------------------------------------------------------ PROFIL
@app.route("/profile")
@login_required
def profile():
    db = get_db()
    u = current_user()
    unlocked = db.execute("""
        SELECT c.number, m.title, m.slug, c.id AS cid FROM purchases p
        JOIN chapters c ON c.id=p.chapter_id JOIN manga m ON m.id=c.manga_id
        WHERE p.user_id=? ORDER BY p.id DESC LIMIT 20""", (u["id"],)).fetchall()
    tpl = """
    <section class="section"><div class="container" style="max-width:820px">
      <div class="sec-head"><div><span class="eyebrow">Hisob</span><h2>{{ user['username'] }}</h2></div></div>

      <div class="panel">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px">
          <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
            <img class="avatar avatar-xl" src="{{ avatar_of(user) }}" alt="">
            <div>
              <div style="color:var(--muted);font-size:.85rem">Sizning ID raqamingiz</div>
              <div style="margin-top:6px"><span class="pageid">ID: {{ user['id'] }}</span></div>
              {% if user['phone'] %}<div style="color:var(--muted);font-size:.82rem;margin-top:8px">📱 +{{ user['phone'] }}</div>{% endif %}
              <div style="color:var(--muted);font-size:.82rem;margin-top:6px">Tanga olishda adminga shu ID ni yuboring.</div>
            </div>
          </div>
          <div style="text-align:right">
            <div style="color:var(--muted);font-size:.85rem">Balans</div>
            <div style="font-family:'Sora';font-size:2rem;font-weight:800;color:var(--gold)">◉ {{ user['coins'] }}</div>
            <a href="{{ url_for('coins') }}" class="btn btn-primary" style="margin-top:6px">Tanga qo'shish</a>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-top:18px">
        <h3>📷 Profil rasmi</h3>
        <form method="post" action="{{ url_for('profile_avatar') }}" enctype="multipart/form-data" class="avatar-form">
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
            <input type="file" name="avatar" accept="image/png,image/jpeg,image/webp,image/gif" required style="flex:1;min-width:200px">
            <button class="btn btn-primary">Avatarni yangilash</button>
          </div>
          <div class="hint">png / jpg / webp / gif formatda. Rasm doira shaklida kesiladi.</div>
        </form>
        {% if user['avatar'] %}
        <form method="post" action="{{ url_for('profile_avatar') }}" style="margin-top:10px">
          <input type="hidden" name="remove" value="1">
          <button class="btn btn-ghost">↺ Standart avatarga qaytarish</button>
        </form>
        {% endif %}
      </div>

      <div class="panel" style="margin-top:18px">
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          {% if user['is_admin'] %}<a href="{{ url_for('admin') }}" class="btn btn-ghost" style="flex:1;min-width:140px">🛠 Admin panel</a>{% endif %}
          <a href="{{ url_for('logout') }}" class="btn btn-ghost" style="flex:1;min-width:140px">Chiqish</a>
        </div>
      </div>

      {% if unlocked %}
      <div class="panel" style="margin-top:18px">
        <h3>Ochilgan boblar</h3>
        <table><tr><th>Manga</th><th>Bob</th></tr>
        {% for r in unlocked %}<tr>
          <td><a href="{{ url_for('manga_detail', slug=r['slug']) }}" style="color:var(--gold)">{{ r['title'] }}</a></td>
          <td>{{ r['number']|int }}-bob</td>
        </tr>{% endfor %}</table>
      </div>
      {% endif %}
    </div></section>
    """
    return render(tpl, unlocked=unlocked, title="Profil")


@app.route("/profile/avatar", methods=["POST"])
@login_required
def profile_avatar():
    """Foydalanuvchi o'z profil rasmini yuklaydi yoki standartga qaytaradi."""
    db = get_db()
    u = current_user()
    if request.form.get("remove"):
        db.execute("UPDATE users SET avatar=NULL WHERE id=?", (u["id"],))
        db.commit()
        flash("Avatar standart holatga qaytarildi.", "ok")
    else:
        url = save_upload(request.files.get("avatar"))
        if url:
            db.execute("UPDATE users SET avatar=? WHERE id=?", (url, u["id"]))
            db.commit()
            flash("✓ Profil rasmi yangilandi.", "ok")
        else:
            flash("Rasm yuklanmadi. png/jpg/webp/gif formatda yuklang.", "err")
    return redirect(url_for("profile"))


# ----------------------------------------------------------- TANGA SOTIB OLISH
@app.route("/coins")
def coins():
    packages = [(20, "10 000"), (50, "22 000"), (120, "50 000"), (300, "110 000")]
    tpl = """
    <section class="section"><div class="container" style="max-width:820px">
      <div class="sec-head"><div><span class="eyebrow">Do'kon</span><h2>Tanga sotib olish</h2></div></div>

      <div class="panel">
        <h3>Qanday sotib olinadi?</h3>
        <ol style="color:var(--muted);margin:10px 0 0 18px;line-height:2">
          <li>Quyidagi <strong style="color:var(--gold)">adminlardan biriga</strong> Telegram orqali yozing.</li>
          <li>Kerakli tanga paketini tanlab, adminga to'lovni amalga oshiring.</li>
          {% if user %}<li>Adminga o'z <strong style="color:var(--gold)">ID: {{ user['id'] }}</strong> raqamingizni yuboring.</li>
          {% else %}<li>Avval <a href="{{ url_for('login') }}" style="color:var(--gold)">telefon raqamingiz bilan kiring</a> — sizga ID beriladi.</li>{% endif %}
          <li>Admin pulni qabul qilgach, tangalar balansingizga tushadi.</li>
        </ol>

        {% if user %}
        <div style="margin:20px 0;padding:16px;background:var(--bg2);border:1px dashed var(--gold);border-radius:12px;text-align:center">
          Sizning ID raqamingiz: <span class="pageid">ID: {{ user['id'] }}</span>
        </div>
        {% endif %}

        <div class="admin-cards">
          <a href="{{ ADMIN1_URL }}" target="_blank" class="admin-card">
            <span class="ic">✈</span>
            <span><b>{{ ADMIN1_NAME }}</b><span>Telegram orqali yozish</span></span>
          </a>
          {% if ADMIN2_URL %}
          <a href="{{ ADMIN2_URL }}" target="_blank" class="admin-card">
            <span class="ic">✈</span>
            <span><b>{{ ADMIN2_NAME }}</b><span>Telegram orqali yozish</span></span>
          </a>
          {% endif %}
        </div>

        {% if TG_CHANNEL or IG_URL %}
        <div class="divider">Bizni kuzatib boring</div>
        <div class="socials" style="justify-content:center">
          {% if TG_CHANNEL %}<a href="{{ TG_CHANNEL }}" target="_blank" class="soc tg"><span class="si">✈</span>Telegram kanal</a>{% endif %}
          {% if IG_URL %}<a href="{{ IG_URL }}" target="_blank" class="soc ig"><span class="si">📷</span>Instagram</a>{% endif %}
        </div>
        {% endif %}
      </div>

      <div class="sec-head" style="margin-top:30px"><h2 style="font-size:1.25rem">Tanga paketlari</h2></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(180px,1fr))">
        {% for amount, price in packages %}
        <div class="panel" style="text-align:center">
          <div style="font-size:2.2rem">◉</div>
          <div style="font-family:'Sora';font-size:1.8rem;font-weight:800;color:var(--gold)">{{ amount }}</div>
          <div style="color:var(--muted);font-size:.85rem">tanga</div>
          <div style="margin:12px 0;font-weight:700">{{ price }} so'm</div>
          <a href="{{ ADMIN1_URL }}" target="_blank" class="btn btn-primary" style="width:100%">Sotib olish</a>
        </div>
        {% endfor %}
      </div>
    </div></section>
    """
    return render(tpl, packages=packages, title="Tanga sotib olish")


# ==================================================================== ADMIN PANEL
@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    stats = {
        "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "manga": db.execute("SELECT COUNT(*) FROM manga").fetchone()[0],
        "chapters": db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0],
        "coins": db.execute("SELECT COALESCE(SUM(coins),0) FROM users").fetchone()[0],
    }
    manga_list = db.execute("SELECT id, title, slug FROM manga ORDER BY id DESC").fetchall()

    tpl = """
    <section class="section"><div class="container">
      <div class="sec-head"><div><span class="eyebrow">Boshqaruv</span><h2>Admin panel</h2></div></div>

      <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(160px,1fr));margin-bottom:24px">
        {% for label,val in [('Foydalanuvchilar',stats['users']),('Mangalar',stats['manga']),('Boblar',stats['chapters']),('Jami tanga',stats['coins'])] %}
        <div class="panel" style="text-align:center">
          <div style="font-family:'Sora';font-size:2rem;font-weight:800;color:var(--gold)">{{ val }}</div>
          <div style="color:var(--muted);font-size:.85rem">{{ label }}</div>
        </div>{% endfor %}
      </div>

      <div class="row-2">
        <a href="{{ url_for('admin_add_coins') }}" class="panel" style="display:block">
          <h3>◉ Tanga qo'shish</h3>
          <p style="color:var(--muted);font-size:.9rem">Foydalanuvchi ID si bo'yicha balansga tanga soling.</p>
        </a>
        <a href="{{ url_for('admin_add_manga') }}" class="panel" style="display:block">
          <h3>+ Manga qo'shish</h3>
          <p style="color:var(--muted);font-size:.9rem">Yangi manga/manhwa qo'shing.</p>
        </a>
        <a href="{{ url_for('admin_add_chapter') }}" class="panel" style="display:block">
          <h3>+ Bob qo'shish (PDF)</h3>
          <p style="color:var(--muted);font-size:.9rem">Bitta PDF yuklang — sahifalarga o'zi bo'linadi.</p>
        </a>
        <a href="{{ url_for('admin_users') }}" class="panel" style="display:block">
          <h3>👥 Foydalanuvchilar</h3>
          <p style="color:var(--muted);font-size:.9rem">Barcha foydalanuvchilar ro'yxati.</p>
        </a>
        <a href="{{ url_for('admin_settings') }}" class="panel" style="display:block">
          <h3>🎨 Sayt sozlamalari</h3>
          <p style="color:var(--muted);font-size:.9rem">Standart avatar, Instagram va Telegram kanal havolalari, adminlar.</p>
        </a>
      </div>

      <div class="panel" style="margin-top:20px">
        <h3>✈ Telegram kirish boti</h3>
        <p style="color:var(--muted);font-size:.9rem;margin-bottom:12px">
          Bot: {% if BOT_ENABLED %}<strong style="color:#8ef0b0">@{{ BOT_USERNAME }} — sozlangan</strong>
          {% else %}<strong style="color:var(--pink)">sozlanmagan</strong> (Railway Variables ga
          TELEGRAM_BOT_TOKEN va TELEGRAM_BOT_USERNAME qo'shing){% endif %}</p>
        <a href="{{ url_for('tg_set_webhook') }}" class="btn btn-tg">🔗 Webhookni ulash / yangilash</a>
        <div class="hint">Har safar deploy manzili o'zgarsa shu tugmani bir marta bosing.</div>
      </div>

      <div class="panel" style="margin-top:20px">
        <h3>Barcha mangalar boshqaruvi (O'chirish)</h3>
        {% if manga_list %}
        <table>
          <tr><th>Manga nomi</th><th>Harakat</th></tr>
          {% for m in manga_list %}
          <tr>
            <td><a href="{{ url_for('manga_detail', slug=m['slug']) }}" target="_blank" style="color:var(--gold);font-weight:600">{{ m['title'] }}</a></td>
            <td>
              <form method="post" action="{{ url_for('admin_delete_manga', manga_id=m['id']) }}" onsubmit="return confirm('Haqiqatdan ham o‘chirmoqchimisiz?');" style="display:inline">
                <button class="btn btn-danger" style="padding:6px 12px; font-size:0.85rem">🗑 O'chirish</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </table>
        {% else %}
        <p style="color:var(--muted)">Tizimda hali manga mavjud emas.</p>
        {% endif %}
      </div>

    </div></section>
    """
    return render(tpl, stats=stats, manga_list=manga_list, title="Admin")


# ------------------------------------------------- ADMIN: SAYT SOZLAMALARI
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    """Standart avatar, ijtimoiy tarmoq havolalari va adminlar ro'yxati."""
    if request.method == "POST":
        # --- Standart avatar ---
        if request.form.get("reset_avatar"):
            set_setting("default_avatar", "")
            flash("Standart avatar sayt avatariga qaytarildi.", "ok")
        else:
            up = save_upload(request.files.get("default_avatar_file"))
            av_url = request.form.get("default_avatar_url", "").strip()
            if up:
                set_setting("default_avatar", up)
            elif av_url:
                set_setting("default_avatar", av_url)

        # --- Ijtimoiy tarmoqlar va adminlar ---
        for key in ("instagram_url", "tg_channel_url",
                    "admin1_url", "admin1_name",
                    "admin2_url", "admin2_name"):
            set_setting(key, request.form.get(key, "").strip())

        flash("✓ Sozlamalar saqlandi.", "ok")
        return redirect(url_for("admin_settings"))

    vals = {k: get_setting(k) for k in (
        "default_avatar", "instagram_url", "tg_channel_url",
        "admin1_url", "admin1_name", "admin2_url", "admin2_name")}

    tpl = """
    <section class="section"><div class="container" style="max-width:640px">
      <div class="sec-head"><div><span class="eyebrow">Admin</span><h2>Sayt sozlamalari</h2></div>
        <a href="{{ url_for('admin') }}">← Panelga</a></div>

      <form method="post" enctype="multipart/form-data">

        <div class="panel">
          <h3>🖼 Standart avatar</h3>
          <p style="color:var(--muted);font-size:.88rem">Rasm qo'ymagan barcha foydalanuvchilarda
            shu avatar ko'rinadi. Hozirgisi:</p>
          <div style="display:flex;align-items:center;gap:16px;margin:14px 0">
            <img class="avatar avatar-xl" style="width:84px;height:84px"
                 src="{{ vals['default_avatar'] or url_for('default_avatar') }}" alt="">
            <div class="hint">{{ 'Admin yuklagan rasm' if vals['default_avatar'] else 'Saytning o\\'z avatari (o\\'rnatilgan)' }}</div>
          </div>
          <label>Yangi rasm yuklash</label>
          <input type="file" name="default_avatar_file" accept="image/*">
          <label>yoki rasm URL manzili</label>
          <input name="default_avatar_url" placeholder="https://...">
          <div class="check"><input type="checkbox" name="reset_avatar" id="rsav">
            <label for="rsav" style="margin:0">Saytning o'z avatariga qaytarish</label></div>
        </div>

        <div class="panel" style="margin-top:18px">
          <h3>🌐 Ijtimoiy tarmoqlar</h3>
          <p style="color:var(--muted);font-size:.88rem">Havola qo'yilsa — sayt pastida va
            "Tanga olish" sahifasida chiroyli tugma bo'lib chiqadi. Bo'sh qoldirsangiz ko'rinmaydi.</p>
          <label>📢 Telegram kanal havolasi</label>
          <input name="tg_channel_url" value="{{ vals['tg_channel_url'] }}" placeholder="https://t.me/kanal_nomi">
          <label>📷 Instagram havolasi</label>
          <input name="instagram_url" value="{{ vals['instagram_url'] }}" placeholder="https://instagram.com/sahifa_nomi">
        </div>

        <div class="panel" style="margin-top:18px">
          <h3>✈ Adminlar (bog'lanish uchun)</h3>
          <div class="row-2">
            <div><label>1-admin ismi</label>
              <input name="admin1_name" value="{{ vals['admin1_name'] }}" placeholder="Bosh admin"></div>
            <div><label>1-admin Telegram havolasi</label>
              <input name="admin1_url" value="{{ vals['admin1_url'] }}" placeholder="{{ TELEGRAM_ADMIN }}"></div>
          </div>
          <div class="hint">Bo'sh qoldirilsa, koddagi asosiy admin havolasi ishlatiladi.</div>
          <div class="row-2" style="margin-top:8px">
            <div><label>2-admin ismi</label>
              <input name="admin2_name" value="{{ vals['admin2_name'] }}" placeholder="masalan: Yordamchi admin"></div>
            <div><label>2-admin Telegram havolasi</label>
              <input name="admin2_url" value="{{ vals['admin2_url'] }}" placeholder="https://t.me/username"></div>
          </div>
          <div class="hint">2-admin havolasi qo'yilsa — "Tanga olish" sahifasi va footerda ikkinchi karta paydo bo'ladi.</div>
        </div>

        <button class="btn btn-primary" style="width:100%;margin-top:18px">💾 Sozlamalarni saqlash</button>
      </form>
    </div></section>
    """
    return render(tpl, vals=vals, TELEGRAM_ADMIN=TELEGRAM_ADMIN, title="Sayt sozlamalari")


@app.route("/admin/delete-manga/<int:manga_id>", methods=["POST"])
@admin_required
def admin_delete_manga(manga_id):
    db = get_db()
    manga = db.execute("SELECT title FROM manga WHERE id=?", (manga_id,)).fetchone()
    if manga:
        db.execute("DELETE FROM manga WHERE id=?", (manga_id,))
        db.commit()
        flash(f"✓ '{manga['title']}' muvaffaqiyatli o'chirib tashlandi.", "ok")
    else:
        flash("Manga topilmadi.", "err")
    return redirect(url_for("admin"))


@app.route("/admin/add-coins", methods=["GET", "POST"])
@admin_required
def admin_add_coins():
    db = get_db()
    admin_u = current_user()
    found = None
    uid_q = request.args.get("uid") or request.form.get("uid")
    if uid_q and str(uid_q).isdigit():
        found = db.execute("SELECT * FROM users WHERE id=?", (int(uid_q),)).fetchone()

    if request.method == "POST" and request.form.get("action") == "add":
        target_id = request.form.get("uid", "")
        amount = request.form.get("amount", "")
        note = request.form.get("note", "").strip()
        if not target_id.isdigit() or not amount.lstrip("-").isdigit():
            flash("ID va miqdorni to'g'ri kiriting.", "err")
        else:
            target = db.execute("SELECT * FROM users WHERE id=?", (int(target_id),)).fetchone()
            if not target:
                flash("Bunday ID li foydalanuvchi topilmadi.", "err")
            else:
                amt = int(amount)
                db.execute("UPDATE users SET coins=coins+? WHERE id=?", (amt, target["id"]))
                db.execute(
                    "INSERT INTO transactions (user_id, amount, admin_id, note, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (target["id"], amt, admin_u["id"], note or "Admin orqali to'ldirildi", now()))
                db.commit()
                flash(f"✓ {target['username']} (ID:{target['id']}) balansiga {amt} {COIN_NAME} qo'shildi.", "ok")
                return redirect(url_for("admin_add_coins", uid=target["id"]))

    tpl = """
    <section class="section"><div class="container" style="max-width:620px">
      <div class="sec-head"><div><span class="eyebrow">Admin · To'lov</span><h2>Tanga qo'shish</h2></div>
        <a href="{{ url_for('admin') }}">← Panelga</a></div>

      <div class="panel">
        <form method="get">
          <label>Foydalanuvchi ID raqami (adminga yuborilgan)</label>
          <div style="display:flex;gap:10px">
            <input name="uid" value="{{ uid_q or '' }}" placeholder="masalan: 5" autofocus>
            <button class="btn btn-ghost">Qidirish</button>
          </div>
        </form>

        {% if uid_q and not found %}
          <p style="color:var(--pink);margin-top:14px">ID: {{ uid_q }} — bunday foydalanuvchi topilmadi.</p>
        {% endif %}

        {% if found %}
        <div style="margin-top:18px;padding:16px;background:var(--bg2);border:1px solid var(--border);border-radius:12px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="display:flex;gap:12px;align-items:center">
              <img class="avatar" style="width:46px;height:46px" src="{{ avatar_of(found) }}" alt="">
              <div><div style="font-weight:700;font-size:1.1rem">{{ found['username'] }}</div>
                <span class="pageid">ID: {{ found['id'] }}</span>
                {% if found['phone'] %}<div style="color:var(--muted);font-size:.82rem;margin-top:6px">📱 +{{ found['phone'] }}</div>{% endif %}</div>
            </div>
            <div style="text-align:right"><div style="color:var(--muted);font-size:.82rem">Hozirgi balans</div>
              <div style="font-family:'Sora';font-size:1.5rem;font-weight:800;color:var(--gold)">◉ {{ found['coins'] }}</div></div>
          </div>
          <form method="post" style="margin-top:16px">
            <input type="hidden" name="action" value="add">
            <input type="hidden" name="uid" value="{{ found['id'] }}">
            <div class="row-2">
              <div><label>Qo'shiladigan tanga</label><input name="amount" type="number" placeholder="masalan: 50" required></div>
              <div><label>Izoh (ixtiyoriy)</label><input name="note" placeholder="Karta orqali to'lov"></div>
            </div>
            <button class="btn btn-primary" style="width:100%;margin-top:18px">◉ Balansga qo'shish</button>
          </form>
        </div>
        {% endif %}
      </div>
    </div></section>
    """
    return render(tpl, found=found, uid_q=uid_q, title="Tanga qo'shish")


@app.route("/admin/users")
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    tpl = """
    <section class="section"><div class="container">
      <div class="sec-head"><div><span class="eyebrow">Admin</span><h2>Foydalanuvchilar</h2></div>
        <a href="{{ url_for('admin') }}">← Panelga</a></div>
      <div class="panel">
        <table><tr><th></th><th>ID</th><th>Ism</th><th>Telefon</th><th>Tanga</th><th>Rol</th><th></th></tr>
        {% for u in users %}<tr>
          <td><img class="avatar" style="width:34px;height:34px" src="{{ avatar_of(u) }}" alt=""></td>
          <td><span class="pageid">{{ u['id'] }}</span></td>
          <td>{{ u['username'] }}</td>
          <td style="color:var(--muted)">{{ ('+' + u['phone']) if u['phone'] else '—' }}</td>
          <td style="color:var(--gold);font-weight:700">◉ {{ u['coins'] }}</td>
          <td>{{ 'Admin' if u['is_admin'] else 'Foydalanuvchi' }}</td>
          <td><a href="{{ url_for('admin_add_coins', uid=u['id']) }}" class="btn btn-ghost" style="padding:6px 12px">Tanga qo'shish</a></td>
        </tr>{% endfor %}</table>
      </div>
    </div></section>
    """
    return render(tpl, users=users, title="Foydalanuvchilar")


@app.route("/admin/add-manga", methods=["GET", "POST"])
@admin_required
def admin_add_manga():
    if request.method == "POST":
        db = get_db()
        title = request.form.get("title", "").strip()
        desc = request.form.get("description", "").strip()
        author = request.form.get("author", "").strip()
        genres = request.form.get("genres", "").strip()
        status = request.form.get("status", "Davom etadi")
        rating = request.form.get("rating", "0")
        cover_url = request.form.get("cover_url", "").strip()
        cover_file = save_upload(request.files.get("cover_file"))
        cover = cover_file or cover_url

        if not title:
            flash("Nomni kiriting.", "err")
        elif not cover:
            flash("Muqova rasm (URL yoki fayl) kerak.", "err")
        else:
            try:
                rating = float(rating)
            except ValueError:
                rating = 0
            db.execute(
                "INSERT INTO manga (title, slug, description, cover, author, genres, "
                "status, rating, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (title, slugify(title), desc, cover, author, genres, status, rating, now()))
            db.commit()
            flash(f"✓ '{title}' qo'shildi.", "ok")
            return redirect(url_for("admin"))

    tpl = """
    <section class="section"><div class="container" style="max-width:640px">
      <div class="sec-head"><div><span class="eyebrow">Admin</span><h2>Manga qo'shish</h2></div>
        <a href="{{ url_for('admin') }}">← Panelga</a></div>
      <div class="panel">
        <form method="post" enctype="multipart/form-data">
          <label>Nomi *</label><input name="title" required autofocus>
          <label>Tavsif</label><textarea name="description"></textarea>
          <div class="row-2">
            <div><label>Muallif</label><input name="author"></div>
            <div><label>Reyting (0-10)</label><input name="rating" type="number" step="0.1" min="0" max="10" value="0"></div>
          </div>
          <label>Janrlar (vergul bilan)</label><input name="genres" placeholder="Jangari, Sarguzasht, Isekai">
          <label>Holati</label>
          <select name="status"><option>Davom etadi</option><option>Tugallangan</option><option>To'xtatilgan</option></select>
          <label>Muqova — rasm yuklash</label><input type="file" name="cover_file" accept="image/*">
          <label>yoki muqova URL manzili</label><input name="cover_url" placeholder="https://...">
          <button class="btn btn-primary" style="width:100%;margin-top:20px">Saqlash</button>
        </form>
      </div>
    </div></section>
    """
    return render(tpl, title="Manga qo'shish")


@app.route("/admin/add-chapter", methods=["GET", "POST"])
@admin_required
def admin_add_chapter():
    db = get_db()
    mangas = db.execute("SELECT id, title FROM manga ORDER BY title").fetchall()

    if request.method == "POST":
        manga_id = request.form.get("manga_id", "")
        number = request.form.get("number", "")
        ctitle = request.form.get("ctitle", "").strip()
        is_premium = 1 if request.form.get("is_premium") else 0
        coin_cost = request.form.get("coin_cost", "0")
        page_urls = request.form.get("page_urls", "").strip()
        image_files = request.files.getlist("page_files")
        pdf_file = request.files.get("pdf_file")

        if not manga_id.isdigit():
            flash("Manga tanlang.", "err")
            return redirect(url_for("admin_add_chapter"))

        try:
            number = float(number)
        except ValueError:
            flash("Bob raqamini to'g'ri kiriting.", "err")
            return redirect(url_for("admin_add_chapter"))

        # 1) Avval PDF ni sahifalarga aylantiramiz (asosiy usul)
        pdf_urls, pdf_err = pdf_to_page_urls(pdf_file)
        if pdf_err:
            flash(pdf_err, "err")
            return redirect(url_for("admin_add_chapter"))

        cost = int(coin_cost) if coin_cost.isdigit() and is_premium else 0
        db.execute(
            "INSERT INTO chapters (manga_id, number, title, is_premium, coin_cost, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (int(manga_id), number, ctitle, is_premium, cost, now()))
        cid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        pg = 0
        # PDF sahifalari
        for url in (pdf_urls or []):
            pg += 1
            db.execute("INSERT INTO pages (chapter_id, page_number, image) VALUES (?,?,?)",
                       (cid, pg, url))
        # Qo'shimcha: alohida rasm fayllari (ixtiyoriy)
        for f in image_files:
            url = save_upload(f)
            if url:
                pg += 1
                db.execute("INSERT INTO pages (chapter_id, page_number, image) VALUES (?,?,?)",
                           (cid, pg, url))
        # Qo'shimcha: URL lar (ixtiyoriy)
        for line in page_urls.splitlines():
            line = line.strip()
            if line:
                pg += 1
                db.execute("INSERT INTO pages (chapter_id, page_number, image) VALUES (?,?,?)",
                           (cid, pg, line))

        if pg == 0:
            # Bo'sh bob — hech narsa yuklanmagan
            db.execute("DELETE FROM chapters WHERE id=?", (cid,))
            db.commit()
            flash("Hech qanday sahifa yuklanmadi. PDF yoki rasm qo'shing.", "err")
            return redirect(url_for("admin_add_chapter"))

        db.commit()
        flash(f"✓ Bob qo'shildi ({pg} ta sahifa).", "ok")
        return redirect(url_for("admin_add_chapter"))

    tpl = """
    <section class="section"><div class="container" style="max-width:640px">
      <div class="sec-head"><div><span class="eyebrow">Admin</span><h2>Bob qo'shish</h2></div>
        <a href="{{ url_for('admin') }}">← Panelga</a></div>

      {% if not mangas %}
        <div class="panel"><p style="color:var(--muted)">Avval manga qo'shing.</p>
        <a href="{{ url_for('admin_add_manga') }}" class="btn btn-primary" style="margin-top:14px">Manga qo'shish</a></div>
      {% else %}
      <div class="panel">
        <form method="post" enctype="multipart/form-data">
          <label>Manga *</label>
          <select name="manga_id" required>
            {% for m in mangas %}<option value="{{ m['id'] }}">{{ m['title'] }}</option>{% endfor %}
          </select>
          <div class="row-2">
            <div><label>Bob raqami *</label><input name="number" type="number" step="0.1" placeholder="1" required></div>
            <div><label>Bob nomi (ixtiyoriy)</label><input name="ctitle"></div>
          </div>
          <div class="check"><input type="checkbox" name="is_premium" id="prem"><label for="prem" style="margin:0">Premium bob (tanga evaziga)</label></div>
          <label>Narxi (tanga) — premium bo'lsa</label><input name="coin_cost" type="number" value="0">

          <div style="margin-top:20px;padding:16px;background:var(--bg2);border:1px dashed var(--gold);border-radius:12px">
            <label style="margin-top:0;color:var(--gold-soft)">📄 PDF fayl yuklash (tavsiya etiladi)</label>
            <input type="file" name="pdf_file" accept="application/pdf">
            <div class="hint">Butun bobni bitta PDF qilib yuklang — har bir sahifa avtomatik ajratiladi.</div>
          </div>

          <details style="margin-top:14px">
            <summary style="cursor:pointer;color:var(--muted);font-size:.86rem;font-weight:600">Yoki rasm/URL bilan yuklash (ixtiyoriy)</summary>
            <label>Sahifa rasmlari (bir nechta tanlash mumkin)</label>
            <input type="file" name="page_files" accept="image/*" multiple>
            <label>yoki rasm URL lari (har qatorga bittadan)</label>
            <textarea name="page_urls" placeholder="https://.../1.jpg&#10;https://.../2.jpg"></textarea>
          </details>

          <button class="btn btn-primary" style="width:100%;margin-top:20px">Bobni saqlash</button>
        </form>
      </div>
      {% endif %}
    </div></section>
    """
    return render(tpl, mangas=mangas, title="Bob qo'shish")


# --------------------------------------------------------------------- XATOLAR
@app.errorhandler(403)
def e403(e):
    return page('<div class="empty"><h2>403 — Ruxsat yo\'q</h2>'
                '<p>Bu sahifa faqat admin uchun.</p></div>', "403"), 403


@app.errorhandler(404)
def e404(e):
    return page('<div class="empty"><h2>404 — Topilmadi</h2>'
                '<p><a href="/" style="color:var(--gold)">Bosh sahifaga qaytish</a></p></div>', "404"), 404


# ===================================================================== ISHGA TUSHISH
# Gunicorn ham, "python app.py" ham ishlashi uchun bazani import paytida tayyorlaymiz.
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print(f"  {SITE_NAME} ishga tushdi:  http://0.0.0.0:{port}")
    print(f"  Zaxira admin:  login={ADMIN_LOGIN}  parol={ADMIN_PASSWORD}")
    print(f"  Telegram bot:  {'@' + TELEGRAM_BOT_USERNAME if TELEGRAM_BOT_TOKEN else 'sozlanmagan'}")
    print("=" * 60)
    app.run(debug=False, host="0.0.0.0", port=port)