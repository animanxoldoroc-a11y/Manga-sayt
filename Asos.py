# -*- coding: utf-8 -*-
"""
==========================================================================
  MANGA OLAMI  —  to'liq bitta fayldagi manga/manhwa o'qish sayti
==========================================================================
  Texnologiya : Python + Flask + SQLite  (tashqi kutubxona shart emas)
  Muallif uchun: Abdulahad
  Railway uchun moslashtirilgan variant (2026)
==========================================================================
"""

import os
import sqlite3
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, request, session, redirect, url_for, g,
    render_template_string, flash, abort, send_from_directory,
)
from werkzeug.security import generate_password_hash, check_password_hash

# ------------------------------------------------------------------ SOZLAMALAR
SITE_NAME = "Manga olami"
TELEGRAM_ADMIN = "https://t.me/animan_only"
ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "admin123"
COIN_NAME = "tanga"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# RAILWAY UCHUN MUHIM: Ma'lumotlar o'chib ketmasligi uchun bazani xavfsizroq joyga qo'yamiz.
# Agar Railway-da "Volume" ulasangiz, uning yo'lini muhit o'zgaruvchisiga (masalan: VOLUME_PATH) yozib qo'ying.
RAILWAY_VOLUME = os.environ.get("VOLUME_PATH", BASE_DIR)
DB_PATH = os.path.join(RAILWAY_VOLUME, "manga_olami.db")
UPLOAD_DIR = os.path.join(RAILWAY_VOLUME, "uploads")

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
# Maxfiy kalit har safar restart bo'lganda o'zgarib ketmasligi uchun Railway o'zgaruvchisidan yoki qat'iy kalitdan foydalanamiz
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "manga-olami-super-secret-key-12345")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024


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


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT UNIQUE NOT NULL,
            password     TEXT NOT NULL,
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
        """
    )
    db.commit()

    # Boshlang'ich admin
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


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.context_processor
def inject_globals():
    return dict(
        SITE_NAME=SITE_NAME, COIN_NAME=COIN_NAME,
        TELEGRAM_ADMIN=TELEGRAM_ADMIN, user=current_user(),
    )


# ================================================================= DIZAYN / CSS
CSS = """
:root{
  --bg:#0d0b1a; --bg2:#120f24; --surface:#171334; --surface2:#1e1942;
  --border:#2c2658; --text:#ece9ff; --muted:#9a94c4;
  --gold:#f5b942; --gold-soft:#ffcf6b; --pink:#ff4d6d; --violet:#8b5cf6;
  --radius:16px; --shadow:0 10px 40px rgba(0,0,0,.45);
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:
    radial-gradient(900px 500px at 12% -10%, rgba(139,92,246,.18), transparent 60%),
    radial-gradient(800px 500px at 100% 0%, rgba(245,185,66,.10), transparent 55%),
    var(--bg);
  color:var(--text); font-family:'Inter',system-ui,-apple-system,sans-serif;
  min-height:100vh; line-height:1.55;
}
a{color:inherit;text-decoration:none}
img{display:block;max-width:100%}
.container{max-width:1180px;margin:0 auto;padding:0 20px}

/* ---- Navbar ---- */
.nav{position:sticky;top:0;z-index:50;
  background:rgba(13,11,26,.82);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border)}
.nav-in{display:flex;align-items:center;gap:22px;height:66px}
.brand{font-family:'Unbounded',sans-serif;font-weight:800;font-size:1.35rem;
  letter-spacing:.5px;background:linear-gradient(100deg,var(--gold),var(--pink));
  -webkit-background-clip:text;background-clip:text;color:transparent;white-space:nowrap}
.nav-links{display:flex;gap:6px;flex:1;flex-wrap:wrap}
.nav-links a{padding:8px 14px;border-radius:10px;color:var(--muted);
  font-weight:600;font-size:.95rem;transition:.15s}
.nav-links a:hover{color:var(--text);background:var(--surface)}
.nav-right{display:flex;align-items:center;gap:10px}
.coin-pill{display:flex;align-items:center;gap:7px;padding:7px 14px;
  border-radius:999px;background:linear-gradient(120deg,#3a2f10,#2a2450);
  border:1px solid var(--gold);font-weight:700;color:var(--gold-soft)}
.coin-pill .dot{width:16px;height:16px;border-radius:50%;
  background:radial-gradient(circle at 35% 30%,#ffe6a0,var(--gold));
  box-shadow:0 0 10px rgba(245,185,66,.6)}
.btn{display:inline-flex;align-items:center;gap:8px;padding:9px 18px;
  border-radius:11px;font-weight:700;font-size:.95rem;cursor:pointer;
  border:1px solid transparent;transition:.15s;white-space:nowrap}
.btn-primary{background:linear-gradient(120deg,var(--gold),var(--pink));color:#1a1030}
.btn-primary:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-danger{background:linear-gradient(120deg,var(--pink),#ff1a40);color:#fff}
.btn-danger:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-ghost{background:var(--surface);border-color:var(--border);color:var(--text)}
.btn-ghost:hover{background:var(--surface2)}
.btn-tg{background:linear-gradient(120deg,#2aa9e0,#1c7fc4);color:#fff}

/* ---- Hero ---- */
.hero{position:relative;padding:56px 0 34px;overflow:hidden}
.hero h1{font-family:'Sora',sans-serif;font-size:clamp(2rem,5vw,3.4rem);
  font-weight:800;line-height:1.05;max-width:720px;letter-spacing:-.5px}
.hero p{color:var(--muted);margin-top:16px;font-size:1.08rem;max-width:560px}
.hero .cta{display:flex;gap:12px;margin-top:26px;flex-wrap:wrap}

/* ---- Sections ---- */
.section{padding:34px 0}
.sec-head{display:flex;align-items:baseline;justify-content:space-between;
  margin-bottom:20px;gap:14px;flex-wrap:wrap}
.sec-head h2{font-family:'Sora',sans-serif;font-size:1.5rem;font-weight:700}
.sec-head .eyebrow{color:var(--gold);font-weight:700;font-size:.8rem;
  letter-spacing:.14em;text-transform:uppercase;display:block;margin-bottom:6px}
.sec-head a{color:var(--muted);font-weight:600;font-size:.9rem}
.sec-head a:hover{color:var(--gold)}

/* ---- Manga grid ---- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:20px}
.card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);overflow:hidden;transition:.18s;position:relative}
.card:hover{transform:translateY(-4px);border-color:var(--violet);box-shadow:var(--shadow)}
.card .cover{aspect-ratio:5/7;width:100%;object-fit:cover;background:var(--surface2)}
.card .body{padding:12px 13px 14px}
.card .title{font-weight:700;font-size:.98rem;line-height:1.25;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .genres{color:var(--muted);font-size:.78rem;margin-top:5px}
.card .rating{position:absolute;top:10px;left:10px;padding:4px 9px;border-radius:8px;
  background:rgba(13,11,26,.82);border:1px solid var(--gold);color:var(--gold-soft);
  font-weight:700;font-size:.8rem;backdrop-filter:blur(4px)}
.badge-prem{position:absolute;top:10px;right:10px;padding:3px 8px;border-radius:8px;
  background:linear-gradient(120deg,var(--gold),var(--pink));color:#1a1030;
  font-weight:800;font-size:.68rem}

/* ---- List ---- */
.rows{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.row{display:flex;gap:13px;padding:12px;background:var(--surface);
  border:1px solid var(--border);border-radius:14px;transition:.15s}
.row:hover{border-color:var(--violet)}
.row img{width:56px;height:76px;object-fit:cover;border-radius:9px;flex-shrink:0}
.row .meta{min-width:0}
.row .meta .t{font-weight:700;font-size:.95rem;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.row .meta .g{color:var(--muted);font-size:.78rem;margin-top:3px}
.row .meta .c{color:var(--gold);font-size:.82rem;font-weight:600;margin-top:6px}

/* ---- Panels / forms ---- */
.panel{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:26px}
.panel h3{font-family:'Sora',sans-serif;font-size:1.2rem;margin-bottom:16px}
label{display:block;font-weight:600;font-size:.88rem;margin:14px 0 6px;color:var(--muted)}
input,textarea,select{width:100%;padding:12px 14px;border-radius:11px;
  background:var(--bg2);border:1px solid var(--border);color:var(--text);
  font-size:.98rem;font-family:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--violet)}
textarea{min-height:100px;resize:vertical}
.row-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.form-wrap{max-width:440px;margin:40px auto}
.check{display:flex;align-items:center;gap:10px;margin-top:14px}
.check input{width:auto}

/* ---- Reader ---- */
.reader{max-width:800px;margin:0 auto;padding:20px}
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
.chapter:hover{border-color:var(--violet);background:var(--surface2)}
.chapter .cprice{color:var(--gold);font-weight:700;font-size:.86rem}
.chapter .free">Bepul</span>

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

@media(max-width:720px){
  .rows{grid-template-columns:1fr}
  .detail-top{grid-template-columns:1fr}
  .detail-top .cover{width:170px}
  .row-2{grid-template-columns:1fr}
  .nav-links{display:none}
}
"""

# ================================================================ ASOSIY SHABLON
BASE = """
<!doctype html><html lang="uz"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0d0b1a">
<title>{{ title or SITE_NAME }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Sora:wght@600;700;800&family=Unbounded:wght@700;800&display=swap" rel="stylesheet">
<style>{{ css|safe }}</style>
</head><body>

<nav class="nav"><div class="container nav-in">
  <a href="{{ url_for('index') }}" class="brand">◈ {{ SITE_NAME }}</a>
  <div class="nav-links">
    <a href="{{ url_for('index') }}">Bosh sahifa</a>
    <a href="{{ url_for('catalog') }}">Katalog</a>
    {% if user %}<a href="{{ url_for('bookmarks') }}">Saqlanganlar</a>{% endif %}
    <a href="{{ url_for('coins') }}">Tanga olish</a>
    {% if user and user['is_admin'] %}<a href="{{ url_for('admin') }}">Admin panel</a>{% endif %}
  </div>
  <div class="nav-right">
    {% if user %}
      <a href="{{ url_for('coins') }}" class="coin-pill"><span class="dot"></span>{{ user['coins'] }}</a>
      <a href="{{ url_for('profile') }}" class="btn btn-ghost">{{ user['username'] }}</a>
      <a href="{{ url_for('logout') }}" class="btn btn-ghost">Chiqish</a>
    {% else %}
      <a href="{{ url_for('login') }}" class="btn btn-ghost">Kirish</a>
      <a href="{{ url_for('register') }}" class="btn btn-primary">Ro'yxatdan o'tish</a>
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

<footer><div class="container foot-in">
  <div style="max-width:340px">
    <div class="brand">◈ {{ SITE_NAME }}</div>
    <p style="margin-top:12px;font-size:.92rem">Eng sara manga, manhwa va manhualarni o'zbek tilida sifatli tarjimada o'qing.</p>
  </div>
  <div class="foot-links">
    <strong style="color:var(--text)">Navigatsiya</strong>
    <a href="{{ url_for('index') }}">Bosh sahifa</a>
    <a href="{{ url_for('catalog') }}">Katalog</a>
    <a href="{{ url_for('coins') }}">Tanga sotib olish</a>
  </div>
  <div class="foot-links">
    <strong style="color:var(--text)">Aloqa</strong>
    <a href="{{ TELEGRAM_ADMIN }}" target="_blank">Telegram admin</a>
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
    top = db.execute("SELECT * FROM manga ORDER BY rating DESC LIMIT 10").fetchall()

    tpl = """
    <section class="hero"><div class="container">
      <h1>Manga olamiga xush kelibsiz</h1>
      <p>Minglab boblar, sara tarjimalar va yangi chiqqan manhwalar — hammasi bir joyda, o'zbek tilida.</p>
      <div class="cta">
        <a href="{{ url_for('catalog') }}" class="btn btn-primary">Katalogni ko'rish</a>
        <a href="{{ url_for('coins') }}" class="btn btn-ghost"><span style="color:var(--gold)">◉</span> Tanga sotib olish</a>
      </div>
    </div></section>

    <section class="section"><div class="container">
      <div class="sec-head">
        <div><span class="eyebrow">Trend</span><h2>Mashhur asarlar</h2></div>
        <a href="{{ url_for('catalog') }}?sort=rating">Barchasi →</a>
      </div>
      {% if popular %}
      <div class="grid">
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
    return render(tpl, popular=popular, latest=latest, top=top,
                  card=card_macro, title=SITE_NAME)


def card_macro(m):
    prem = get_db().execute(
        "SELECT 1 FROM chapters WHERE manga_id=? AND is_premium=1 LIMIT 1",
        (m["id"],)).fetchone()
    return render_template_string("""
    <a href="{{ url_for('manga_detail', slug=m['slug']) }}" class="card">
      <div style="position:relative">
        <img class="cover" src="{{ m['cover'] }}" alt="{{ m['title'] }}">
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
        sql += " AND title LIKE ?";
        params.append(f"%{q}%")
    if genre:
        sql += " AND genres LIKE ?";
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
    <div class="container detail-top">
      <div>
        <img class="cover" src="{{ m['cover'] }}" alt="{{ m['title'] }}">
        {% if user %}
        <form method="post" action="{{ url_for('toggle_bookmark', manga_id=m['id']) }}" style="margin-top:14px">
          <button class="btn {{ 'btn-primary' if not bookmarked else 'btn-ghost' }}" style="width:100%">
            {{ '★ Saqlangan' if bookmarked else '☆ Saqlash' }}
          </button>
        </form>
        {% endif %}

        {# ADMIN UCHUN MANGA O'CHIRISH TUGMASI #}
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
          {% for g in m['genres'].split(',') %}<span class="tag">{{ g.strip() }}</span>{% endfor %}
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


# ----------------------------------------------------------------- AUTH
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        db = get_db()
        if len(username) < 3:
            flash("Login kamida 3 ta belgidan iborat bo'lsin.", "err")
        elif len(pw) < 4:
            flash("Parol kamida 4 ta belgidan iborat bo'lsin.", "err")
        elif pw != pw2:
            flash("Parollar mos kelmadi.", "err")
        elif db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            flash("Bu login band.", "err")
        else:
            db.execute(
                "INSERT INTO users (username, password, coins, is_admin, created_at) "
                "VALUES (?,?,?,?,?)",
                (username, generate_password_hash(pw), 0, 0, now()))
            db.commit()
            uid = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
            session["uid"] = uid
            flash("Xush kelibsiz! Ro'yxatdan o'tdingiz.", "ok")
            return redirect(url_for("index"))
    tpl = """
    <div class="form-wrap panel">
      <h3>Ro'yxatdan o'tish</h3>
      <form method="post">
        <label>Login</label><input name="username" required autofocus>
        <label>Parol</label><input type="password" name="password" required>
        <label>Parolni takrorlang</label><input type="password" name="password2" required>
        <button class="btn btn-primary" style="width:100%;margin-top:20px">Ro'yxatdan o'tish</button>
      </form>
      <p style="margin-top:16px;color:var(--muted);font-size:.9rem">
        Hisobingiz bormi? <a href="{{ url_for('login') }}" style="color:var(--gold)">Kirish</a></p>
    </div>
    """
    return render(tpl, title="Ro'yxatdan o'tish")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        u = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if u and check_password_hash(u["password"], pw):
            session["uid"] = u["id"]
            flash("Tizimga kirdingiz.", "ok")
            nxt = request.args.get("next")
            return redirect(nxt or url_for("index"))
        flash("Login yoki parol xato.", "err")
    tpl = """
    <div class="form-wrap panel">
      <h3>Kirish</h3>
      <form method="post">
        <label>Login</label><input name="username" required autofocus>
        <label>Parol</label><input type="password" name="password" required>
        <button class="btn btn-primary" style="width:100%;margin-top:20px">Kirish</button>
      </form>
      <p style="margin-top:16px;color:var(--muted);font-size:.9rem">
        Hisobingiz yo'qmi? <a href="{{ url_for('register') }}" style="color:var(--gold)">Ro'yxatdan o'tish</a></p>
    </div>
    """
    return render(tpl, title="Kirish")


@app.route("/logout")
def logout():
    session.clear()
    flash("Tizimdan chiqdingiz.", "ok")
    return redirect(url_for("index"))


@app.route("/profile")
@login_required
def profile():
    db = get_db()
    u = current_user()
    txs = db.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (u["id"],)).fetchall()
    unlocked = db.execute("""
        SELECT c.number, m.title, m.slug, c.id AS cid FROM purchases p
        JOIN chapters c ON c.id=p.chapter_id JOIN manga m ON m.id=c.manga_id
        WHERE p.user_id=? ORDER BY p.id DESC LIMIT 20""", (u["id"],)).fetchall()
    tpl = """
    <section class="section"><div class="container" style="max-width:820px">
      <div class="sec-head"><div><span class="eyebrow">Hisob</span><h2>{{ user['username'] }}</h2></div></div>
      <div class="panel">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px">
          <div>
            <div style="color:var(--muted);font-size:.85rem">Sizning ID raqamingiz</div>
            <div style="margin-top:6px"><span class="pageid">ID: {{ user['id'] }}</span></div>
            <div style="color:var(--muted);font-size:.82rem;margin-top:8px">Tanga sotib olishda adminga shu ID ni yuboring.</div>
          </div>
          <div style="text-align:right">
            <div style="color:var(--muted);font-size:.85rem">Balans</div>
            <div style="font-family:'Sora';font-size:2rem;font-weight:800;color:var(--gold)">◉ {{ user['coins'] }}</div>
            <a href="{{ url_for('coins') }}" class="btn btn-primary" style="margin-top:6px">Tanga qo'shish</a>
          </div>
        </div>
      </div>
    </div></section>
    """
    return render(tpl, txs=txs, unlocked=unlocked, title="Profil")


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
          <li>Quyidagi <strong style="color:var(--gold)">Telegram admin</strong> tugmasini bosing.</li>
          <li>Kerakli tanga paketini tanlab, adminga to'lovni amalga oshiring.</li>
          {% if user %}<li>Adminga o'z <strong style="color:var(--gold)">ID: {{ user['id'] }}</strong> raqamingizni yuboring.</li>
          {% else %}<li>Avval <a href="{{ url_for('register') }}" style="color:var(--gold)">ro'yxatdan o'teb qo'ying</a> — sizga ID beriladi.</li>{% endif %}
          <li>Admin pulni qabul qilgach, tangalar balansingizga tushadi.</li>
        </ol>

        {% if user %}
        <div style="margin:20px 0;padding:16px;background:var(--bg2);border:1px dashed var(--gold);border-radius:12px;text-align:center">
          Sizning ID raqamingiz: <span class="pageid">ID: {{ user['id'] }}</span>
        </div>
        {% endif %}

        <a href="{{ TELEGRAM_ADMIN }}" target="_blank" class="btn btn-tg" style="width:100%;justify-content:center;margin-top:10px">
          ✈ Telegram admin bilan bog'lanish</a>
      </div>

      <div class="sec-head" style="margin-top:30px"><h2 style="font-size:1.25rem">Tanga paketlari</h2></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(180px,1fr))">
        {% for amount, price in packages %}
        <div class="panel" style="text-align:center">
          <div style="font-size:2.2rem">◉</div>
          <div style="font-family:'Sora';font-size:1.8rem;font-weight:800;color:var(--gold)">{{ amount }}</div>
          <div style="color:var(--muted);font-size:.85rem">tanga</div>
          <div style="margin:12px 0;font-weight:700">{{ price }} so'm</div>
          <a href="{{ TELEGRAM_ADMIN }}" target="_blank" class="btn btn-primary" style="width:100%">Sotib olish</a>
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
          <h3>+ Bob qo'shish</h3>
          <p style="color:var(--muted);font-size:.9rem">Mavjud mangaga bob va rasmlar yuklang.</p>
        </a>
        <a href="{{ url_for('admin_users') }}" class="panel" style="display:block">
          <h3>👥 Foydalanuvchilar</h3>
          <p style="color:var(--muted);font-size:.9rem">Barcha foydalanuvchilar ro'yxati.</p>
        </a>
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
    if uid_q and uid_q.isdigit():
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
            <div><div style="font-weight:700;font-size:1.1rem">{{ found['username'] }}</div>
              <span class="pageid">ID: {{ found['id'] }}</span></div>
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
        <table><tr><th>ID</th><th>Login</th><th>Tanga</th><th>Rol</th><th></th></tr>
        {% for u in users %}<tr>
          <td><span class="pageid">{{ u['id'] }}</span></td>
          <td>{{ u['username'] }}</td>
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
        files = request.files.getlist("page_files")

        if not manga_id.isdigit():
            flash("Manga tanlang.", "err")
        else:
            try:
                number = float(number)
            except ValueError:
                flash("Bob raqamini to'g'ri kiriting.", "err")
                return redirect(url_for("admin_add_chapter"))
            cost = int(coin_cost) if coin_cost.isdigit() and is_premium else 0
            db.execute(
                "INSERT INTO chapters (manga_id, number, title, is_premium, coin_cost, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (int(manga_id), number, ctitle, is_premium, cost, now()))
            cid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

            pg = 0
            for f in files:
                url = save_upload(f)
                if url:
                    pg += 1
                    db.execute("INSERT INTO pages (chapter_id, page_number, image) VALUES (?,?,?)",
                               (cid, pg, url))
            for line in page_urls.splitlines():
                line = line.strip()
                if line:
                    pg += 1
                    db.execute("INSERT INTO pages (chapter_id, page_number, image) VALUES (?,?,?)",
                               (cid, pg, line))
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
          <label>Sahifa rasmlarini yuklash (bir nechta tanlash mumkin)</label>
          <input type="file" name="page_files" accept="image/*" multiple>
          <label>yoki rasm URL lari (har qatorga bittadan)</label>
          <textarea name="page_urls" placeholder="https://.../1.jpg&#10;https://.../2.jpg"></textarea>
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
if __name__ == "__main__":
    init_db()
    # Railway muhitiga portni dinamik moslash
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print(f"  {SITE_NAME} ishga tushdi:  http://0.0.0.0:{port}")
    print(f"  Admin:  login={ADMIN_LOGIN}  parol={ADMIN_PASSWORD}")
    print("=" * 60)
    # Production muhitda debug=False bo'lgani ma'qul
    app.run(debug=False, host="0.0.0.0", port=port)