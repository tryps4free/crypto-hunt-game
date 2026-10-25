import os, sqlite3, secrets, hashlib
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).resolve().parent
DB = BASE / "crypto_hunt.db"
SECRET_KEY = os.getenv("SESSION_SECRET", "CHANGE-ME-local-only-" + secrets.token_hex(16))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lower()
DAILY_CLICKS = 20
EXTRA_CLICK_PACK = 10
EXTRA_CLICK_COST = 100
ETH_LEVEL_PRICE = "0.0005 ETH (DEMO)"

app = FastAPI(title="Crypto Hunt")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=str(BASE)), name="static")
templates = Jinja2Templates(directory=str(BASE))

NETWORKS = {
    "LTC": {"unlock_level": 1, "label": "Litecoin"},
    "ETH": {"unlock_level": 10, "label": "Ethereum"},
    "BTC": {"unlock_level": 25, "label": "Bitcoin"},
}

def db():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          xp INTEGER NOT NULL DEFAULT 0,
          virtual_coins INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          network TEXT NOT NULL,
          page_number INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'available',
          claimed_by INTEGER,
          claimed_at TEXT,
          result TEXT NOT NULL DEFAULT 'pending',
          reward INTEGER NOT NULL DEFAULT 0,
          UNIQUE(network, page_number),
          FOREIGN KEY(claimed_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          network TEXT NOT NULL,
          page_number INTEGER NOT NULL,
          result TEXT NOT NULL,
          reward INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          UNIQUE(network, page_number),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER,
          action TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_usage (
          user_id INTEGER NOT NULL,
          day TEXT NOT NULL,
          clicks_used INTEGER NOT NULL DEFAULT 0,
          extra_clicks INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(user_id, day),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS game_purchases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          purchase_type TEXT NOT NULL,
          amount TEXT NOT NULL,
          note TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        # Pre-seed a modest set of demo pages. IDs are game page identifiers, not keys.
        for net in NETWORKS:
            for n in range(1, 501):
                c.execute("INSERT OR IGNORE INTO pages(network,page_number) VALUES(?,?)", (net, n))

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"

def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt, expected = encoded.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310_000).hex()
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def now():
    return datetime.now(timezone.utc).isoformat()

def today_key():
    return datetime.now(timezone.utc).date().isoformat()

def xp_needed_for_level(level: int) -> int:
    """Total XP threshold at which a level begins."""
    return 100 * (level - 1) * level // 2

def usage_for(user_id: int):
    day = today_key()
    with db() as c:
        c.execute("INSERT OR IGNORE INTO daily_usage(user_id,day) VALUES(?,?)", (user_id, day))
        row = c.execute("SELECT clicks_used,extra_clicks FROM daily_usage WHERE user_id=? AND day=?", (user_id, day)).fetchone()
    used, extra = row["clicks_used"], row["extra_clicks"]
    return {"used": used, "extra": extra, "limit": DAILY_CLICKS + extra, "remaining": max(0, DAILY_CLICKS + extra - used)}

def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def level_for_xp(xp: int) -> int:
    level = 1
    while xp >= level * 100:
        xp -= level * 100
        level += 1
    return level

def xp_into_level(xp: int):
    level = level_for_xp(xp)
    before = sum(i * 100 for i in range(1, level))
    needed = level * 100
    return xp - before, needed

def check_public_address_balance(network: str, address: str):
    """
    Integration point for a public blockchain explorer/API.
    Accept PUBLIC ADDRESSES only. Do not add private-key/seed collection here.
    Return a decimal/string balance and source metadata when implemented.
    """
    raise NotImplementedError("Configure a public explorer provider for authorized addresses/test puzzles.")

@app.on_event("startup")
def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": None})
    level = level_for_xp(user["xp"])
    current, needed = xp_into_level(user["xp"])
    with db() as c:
        claimed = c.execute("SELECT COUNT(*) n FROM pages WHERE claimed_by=?", (user["id"],)).fetchone()["n"]
        leaderboard = c.execute("SELECT username,xp,virtual_coins FROM users ORDER BY xp DESC, virtual_coins DESC LIMIT 10").fetchall()
    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "request": request, "user": user, "level": level, "xp_current": current,
        "xp_needed": needed, "claimed": claimed, "networks": NETWORKS,
        "leaderboard": leaderboard, "admin": user["username"].lower() == ADMIN_USERNAME and bool(ADMIN_USERNAME),
        "usage": usage_for(user["id"]), "daily_clicks": DAILY_CLICKS,
        "extra_click_pack": EXTRA_CLICK_PACK, "extra_click_cost": EXTRA_CLICK_COST,
        "eth_level_price": ETH_LEVEL_PRICE
    })

@app.post("/register")
def register(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip()
    if len(username) < 3 or len(username) > 24 or not username.replace("_","").isalnum():
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Username: 3–24 letters/numbers/underscore."}, status_code=400)
    if len(password) < 10:
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Use a password with at least 10 characters."}, status_code=400)
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                            (username, hash_password(password), now()))
            uid = cur.lastrowid
            c.execute("INSERT INTO audit_log(user_id,action,created_at) VALUES(?,?,?)", (uid,"register",now()))
        request.session["uid"] = uid
        return RedirectResponse("/", status_code=303)
    except sqlite3.IntegrityError:
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "That username is already taken."}, status_code=400)

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as c:
        user = c.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Incorrect username or password."}, status_code=401)
    request.session["uid"] = user["id"]
    return RedirectResponse("/", status_code=303)

@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

@app.post("/claim")
def claim(request: Request, network: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    network = network.upper()
    if network not in NETWORKS:
        raise HTTPException(400, "Unknown network")
    usage = usage_for(user["id"])
    if usage["remaining"] <= 0:
        raise HTTPException(429, "You have used today's clicks. Buy an extra-click pack or come back tomorrow.")
    level = level_for_xp(user["xp"])
    if level < NETWORKS[network]["unlock_level"]:
        raise HTTPException(403, f"{NETWORKS[network]['label']} unlocks at level {NETWORKS[network]['unlock_level']}.")
    # Atomic allocation: choose an available page, then claim inside one immediate transaction.
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id,page_number FROM pages WHERE network=? AND status='available' ORDER BY RANDOM() LIMIT 1", (network,)).fetchone()
        if not row:
            # Add another batch of unique demo page IDs.
            maxrow = conn.execute("SELECT COALESCE(MAX(page_number),0) m FROM pages WHERE network=?", (network,)).fetchone()
            start = maxrow["m"] + 1
            for n in range(start, start + 500):
                conn.execute("INSERT OR IGNORE INTO pages(network,page_number) VALUES(?,?)", (network,n))
            row = conn.execute("SELECT id,page_number FROM pages WHERE network=? AND status='available' ORDER BY RANDOM() LIMIT 1", (network,)).fetchone()
        conn.execute("UPDATE pages SET status='claimed',claimed_by=?,claimed_at=? WHERE id=? AND status='available'", (user["id"],now(),row["id"]))
        conn.execute("INSERT INTO audit_log(user_id,action,created_at) VALUES(?,?,?)", (user["id"],f"claim:{network}:{row['page_number']}",now()))
        conn.execute("INSERT OR IGNORE INTO daily_usage(user_id,day) VALUES(?,?)", (user["id"], today_key()))
        conn.execute("UPDATE daily_usage SET clicks_used=clicks_used+1 WHERE user_id=? AND day=?", (user["id"], today_key()))
        conn.commit()
    finally:
        conn.close()
    # Starter game rewards are virtual and explicitly simulated.
    xp_reward = {"LTC": 10, "ETH": 20, "BTC": 40}[network]
    virtual_reward = {"LTC": 25, "ETH": 75, "BTC": 150}[network]
    with db() as c:
        c.execute("UPDATE users SET xp=xp+?,virtual_coins=virtual_coins+? WHERE id=?", (xp_reward,virtual_reward,user["id"]))
        c.execute("UPDATE pages SET result='demo-puzzle',reward=? WHERE network=? AND page_number=?", (virtual_reward,network,row["page_number"]))
        c.execute("INSERT INTO events(user_id,network,page_number,result,reward,created_at) VALUES(?,?,?,?,?,?)",
                  (user["id"],network,row["page_number"],"demo-puzzle",virtual_reward,now()))
    return RedirectResponse(f"/result/{network}/{row['page_number']}", status_code=303)

@app.post("/shop/buy-clicks")
def buy_clicks(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    with db() as c:
        fresh = c.execute("SELECT virtual_coins FROM users WHERE id=?", (user["id"],)).fetchone()
        if fresh["virtual_coins"] < EXTRA_CLICK_COST:
            raise HTTPException(400, f"You need {EXTRA_CLICK_COST} VCOIN for an extra-click pack.")
        c.execute("UPDATE users SET virtual_coins=virtual_coins-? WHERE id=?", (EXTRA_CLICK_COST, user["id"]))
        c.execute("INSERT OR IGNORE INTO daily_usage(user_id,day) VALUES(?,?)", (user["id"], today_key()))
        c.execute("UPDATE daily_usage SET extra_clicks=extra_clicks+? WHERE user_id=? AND day=?", (EXTRA_CLICK_PACK, user["id"], today_key()))
        c.execute("INSERT INTO game_purchases(user_id,purchase_type,amount,note,created_at) VALUES(?,?,?,?,?)", (user["id"], "extra_clicks", str(EXTRA_CLICK_COST), "In-game VCOIN purchase", now()))
    return RedirectResponse("/?shop=clicks", status_code=303)

@app.post("/shop/buy-level")
def buy_level(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    current_level = level_for_xp(user["xp"])
    target_level = current_level + 1
    # Explicitly a demo checkout: no wallet connection or real ETH is collected.
    with db() as c:
        c.execute("UPDATE users SET xp=MAX(xp, ?) WHERE id=?", (xp_needed_for_level(target_level), user["id"]))
        c.execute("INSERT INTO game_purchases(user_id,purchase_type,amount,note,created_at) VALUES(?,?,?,?,?)", (user["id"], "level_unlock_demo", "0.0005 ETH (DEMO)", f"Demo unlock to level {target_level}; no real payment", now()))
        c.execute("INSERT INTO audit_log(user_id,action,created_at) VALUES(?,?,?)", (user["id"], f"demo_level_unlock:{target_level}", now()))
    return RedirectResponse("/?shop=level", status_code=303)

@app.get("/result/{network}/{page_number}", response_class=HTMLResponse)
def result(request: Request, network: str, page_number: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    with db() as c:
        row = c.execute("SELECT * FROM pages WHERE network=? AND page_number=? AND claimed_by=?", (network.upper(),page_number,user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Page not found for this user.")
    return templates.TemplateResponse(request=request, name="result.html", context={"request":request,"user":user,"page":row,"networks":NETWORKS})

@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    with db() as c:
        rows = c.execute("SELECT network,page_number,result,reward,created_at FROM events WHERE user_id=? ORDER BY id DESC LIMIT 100",(user["id"],)).fetchall()
    return templates.TemplateResponse(request=request, name="history.html", context={"request":request,"user":user,"rows":rows})

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    user = current_user(request)
    if not user or not ADMIN_USERNAME or user["username"].lower() != ADMIN_USERNAME:
        raise HTTPException(403, "Set ADMIN_USERNAME and log in with that username.")
    with db() as c:
        users = c.execute("SELECT username,xp,virtual_coins,created_at FROM users ORDER BY xp DESC").fetchall()
        stats = {
            "users": c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
            "claimed": c.execute("SELECT COUNT(*) n FROM pages WHERE status='claimed'").fetchone()["n"],
            "pages": c.execute("SELECT COUNT(*) n FROM pages").fetchone()["n"],
        }
    return templates.TemplateResponse(request=request, name="admin.html", context={"request":request,"user":user,"users":users,"stats":stats})

@app.get("/health")
def health():
    return {"ok": True, "mode": "game demo", "private_key_storage": False, "daily_click_limit": DAILY_CLICKS, "real_eth_payments": False}
