import os, sqlite3, secrets, hashlib, random
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from passlib.context import CryptContext

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "crypto_hunt.db"
SECRET_KEY = os.getenv("SESSION_SECRET", "CHANGE-ME-local-only-" + secrets.token_hex(16))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lower()
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Crypto Hunt")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "app"))

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
        """)
        # Pre-seed a modest set of demo pages. IDs are game page identifiers, not keys.
        for net in NETWORKS:
            for n in range(1, 501):
                c.execute("INSERT OR IGNORE INTO pages(network,page_number) VALUES(?,?)", (net, n))

def now():
    return datetime.now(timezone.utc).isoformat()

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
        return templates.TemplateResponse("login.html", {"request": request, "error": None})
    level = level_for_xp(user["xp"])
    current, needed = xp_into_level(user["xp"])
    with db() as c:
        claimed = c.execute("SELECT COUNT(*) n FROM pages WHERE claimed_by=?", (user["id"],)).fetchone()["n"]
        leaderboard = c.execute("SELECT username,xp,virtual_coins FROM users ORDER BY xp DESC, virtual_coins DESC LIMIT 10").fetchall()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "level": level, "xp_current": current,
        "xp_needed": needed, "claimed": claimed, "networks": NETWORKS,
        "leaderboard": leaderboard, "admin": user["username"].lower() == ADMIN_USERNAME and bool(ADMIN_USERNAME)
    })

@app.post("/register")
def register(username: str = Form(...), password: str = Form(...), request: Request = None):
    username = username.strip()
    if len(username) < 3 or len(username) > 24 or not username.replace("_","").isalnum():
        return templates.TemplateResponse("login.html", {"request": request, "error": "Username: 3–24 letters/numbers/underscore."}, status_code=400)
    if len(password) < 10:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Use a password with at least 10 characters."}, status_code=400)
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                            (username, pwd.hash(password), now()))
            uid = cur.lastrowid
            c.execute("INSERT INTO audit_log(user_id,action,created_at) VALUES(?,?,?)", (uid,"register",now()))
        request.session["uid"] = uid
        return RedirectResponse("/", status_code=303)
    except sqlite3.IntegrityError:
        return templates.TemplateResponse("login.html", {"request": request, "error": "That username is already taken."}, status_code=400)

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as c:
        user = c.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not user or not pwd.verify(password, user["password_hash"]):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Incorrect username or password."}, status_code=401)
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

@app.get("/result/{network}/{page_number}", response_class=HTMLResponse)
def result(request: Request, network: str, page_number: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    with db() as c:
        row = c.execute("SELECT * FROM pages WHERE network=? AND page_number=? AND claimed_by=?", (network.upper(),page_number,user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Page not found for this user.")
    return templates.TemplateResponse("result.html", {"request":request,"user":user,"page":row,"networks":NETWORKS})

@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    with db() as c:
        rows = c.execute("SELECT network,page_number,result,reward,created_at FROM events WHERE user_id=? ORDER BY id DESC LIMIT 100",(user["id"],)).fetchall()
    return templates.TemplateResponse("history.html", {"request":request,"user":user,"rows":rows})

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
    return templates.TemplateResponse("admin.html", {"request":request,"user":user,"users":users,"stats":stats})

@app.get("/health")
def health():
    return {"ok": True, "mode": "local starter", "private_key_storage": False}
