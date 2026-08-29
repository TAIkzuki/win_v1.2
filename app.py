
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
import sqlite3, os, secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "wedding.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS guests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        invited_count INTEGER NOT NULL DEFAULT 1,
        attending_count INTEGER,
        submitted INTEGER NOT NULL DEFAULT 0,
        short_fact TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS menu_choices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guest_id INTEGER NOT NULL,
        guest_number INTEGER NOT NULL,
        menu TEXT NOT NULL,
        FOREIGN KEY (guest_id) REFERENCES guests(id)
    );

    CREATE TABLE IF NOT EXISTS drink_choices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guest_id INTEGER NOT NULL,
        guest_number INTEGER NOT NULL,
        drink TEXT NOT NULL,
        FOREIGN KEY (guest_id) REFERENCES guests(id)
    );
    """)
    # Миграция существующей базы: добавляем поле факта, если его ещё нет.
    columns = [row[1] for row in conn.execute("PRAGMA table_info(guests)").fetchall()]
    if "short_fact" not in columns:
        conn.execute("ALTER TABLE guests ADD COLUMN short_fact TEXT")
    conn.commit()
    conn.close()

def admin_required():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return None

@app.context_processor
def inject_config():
    return {"couple": "Алексей & Анастасия", "wedding_date": "25 сентября 2026"}

@app.route("/")
def index():
    return render_template("home.html")

@app.route("/invite/<token>", methods=["GET", "POST"])
def invite(token):
    conn = get_db()
    guest = conn.execute("SELECT * FROM guests WHERE token=?", (token,)).fetchone()
    if not guest:
        conn.close()
        abort(404)

    if request.method == "POST":
        attending = int(request.form.get("attending_count", 0))
        if attending < 0 or attending > guest["invited_count"]:
            flash("Некорректное количество гостей.")
            conn.close()
            return redirect(url_for("invite", token=token))

        short_fact = request.form.get("short_fact", "").strip()
        if len(short_fact) > 500:
            flash("Краткий факт должен быть не длиннее 500 символов.")
            conn.close()
            return redirect(url_for("invite", token=token, edit="1"))

        choices = []
        drinks = []
        if attending:
            for i in range(1, attending + 1):
                menu = request.form.get(f"menu_{i}")
                if menu not in ("meat", "fish", "poultry"):
                    flash("Пожалуйста, выберите блюдо для каждого гостя.")
                    conn.close()
                    return redirect(url_for("invite", token=token))
                choices.append(menu)

                guest_drinks = request.form.getlist(f"drink_{i}")
                allowed_drinks = {"champagne", "white_wine", "red_wine", "cognac", "whisky", "tinctures", "non_alcoholic", "anything_burning", "no_alcohol"}
                if not guest_drinks or any(d not in allowed_drinks for d in guest_drinks):
                    flash("Пожалуйста, выберите предпочтения по напиткам для каждого гостя.")
                    conn.close()
                    return redirect(url_for("invite", token=token))
                if "no_alcohol" in guest_drinks and len(guest_drinks) > 1:
                    flash("Для варианта «Не планирую пить алкоголь» выберите только его.")
                    conn.close()
                    return redirect(url_for("invite", token=token))
                drinks.extend((i, d) for d in guest_drinks)

        conn.execute("DELETE FROM menu_choices WHERE guest_id=?", (guest["id"],))
        for i, menu in enumerate(choices, 1):
            conn.execute(
                "INSERT INTO menu_choices (guest_id, guest_number, menu) VALUES (?, ?, ?)",
                (guest["id"], i, menu)
            )
        conn.execute("DELETE FROM drink_choices WHERE guest_id=?", (guest["id"],))
        for guest_number, drink in drinks:
            conn.execute(
                "INSERT INTO drink_choices (guest_id, guest_number, drink) VALUES (?, ?, ?)",
                (guest["id"], guest_number, drink)
            )
        conn.execute(
            "UPDATE guests SET attending_count=?, short_fact=?, submitted=1 WHERE id=?",
            (attending, short_fact, guest["id"])
        )
        conn.commit()

        # Получаем актуальные ответы, чтобы показать их на странице результата.
        choices = conn.execute(
            "SELECT * FROM menu_choices WHERE guest_id=? ORDER BY guest_number",
            (guest["id"],)
        ).fetchall()
        drinks = conn.execute(
            "SELECT * FROM drink_choices WHERE guest_id=? ORDER BY guest_number, id",
            (guest["id"],)
        ).fetchall()
        conn.close()
        return render_template("thanks.html", guest=guest, choices=choices, drinks=drinks)

    choices = conn.execute(
        "SELECT * FROM menu_choices WHERE guest_id=? ORDER BY guest_number",
        (guest["id"],)
    ).fetchall()
    drinks = conn.execute(
        "SELECT * FROM drink_choices WHERE guest_id=? ORDER BY guest_number, id",
        (guest["id"],)
    ).fetchall()
    conn.close()

    # Если гость уже отвечал и снова открывает свою ссылку,
    # показываем сохранённый результат. Для повторного прохождения
    # используется параметр ?edit=1.
    if guest["submitted"] and request.args.get("edit") != "1":
        return render_template("thanks.html", guest=guest, choices=choices, drinks=drinks)

    return render_template("invite.html", guest=guest, choices=choices, drinks=drinks)

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Неверный пароль.")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin")
def admin():
    r = admin_required()
    if r: return r
    conn = get_db()
    guests = conn.execute("SELECT * FROM guests ORDER BY id DESC").fetchall()
    totals = {
        "invited": conn.execute("SELECT COALESCE(SUM(invited_count),0) FROM guests").fetchone()[0],
        "responded": conn.execute("SELECT COUNT(*) FROM guests WHERE submitted=1").fetchone()[0],
        "attending": conn.execute("SELECT COALESCE(SUM(attending_count),0) FROM guests").fetchone()[0],
        "meat": conn.execute("SELECT COUNT(*) FROM menu_choices WHERE menu='meat'").fetchone()[0],
        "fish": conn.execute("SELECT COUNT(*) FROM menu_choices WHERE menu='fish'").fetchone()[0],
        "poultry": conn.execute("SELECT COUNT(*) FROM menu_choices WHERE menu='poultry'").fetchone()[0],
        "champagne": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='champagne'").fetchone()[0],
        "white_wine": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='white_wine'").fetchone()[0],
        "red_wine": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='red_wine'").fetchone()[0],
        "cognac": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='cognac'").fetchone()[0],
        "whisky": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='whisky'").fetchone()[0],
        "tinctures": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='tinctures'").fetchone()[0],
        "non_alcoholic": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='non_alcoholic'").fetchone()[0],
        "anything_burning": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='anything_burning'").fetchone()[0],
        "no_alcohol": conn.execute("SELECT COUNT(*) FROM drink_choices WHERE drink='no_alcohol'").fetchone()[0],
    }
    conn.close()
    return render_template("admin.html", guests=guests, totals=totals)

@app.route("/admin/add", methods=["GET", "POST"])
def add_guest():
    r = admin_required()
    if r: return r
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            invited_count = int(request.form.get("invited_count", "1"))
        except ValueError:
            invited_count = 1
        if not name or invited_count < 1 or invited_count > 20:
            flash("Введите имя и количество гостей от 1 до 20.")
            return render_template("add_guest.html")
        conn = get_db()
        token = secrets.token_urlsafe(8)
        while conn.execute("SELECT 1 FROM guests WHERE token=?", (token,)).fetchone():
            token = secrets.token_urlsafe(8)
        conn.execute(
            "INSERT INTO guests (name, token, invited_count) VALUES (?, ?, ?)",
            (name, token, invited_count)
        )
        conn.commit()
        conn.close()
        flash("Гость добавлен. Ссылка создана.")
        return redirect(url_for("admin"))
    return render_template("add_guest.html")

@app.route("/admin/delete/<int:guest_id>", methods=["POST"])
def delete_guest(guest_id):
    r = admin_required()
    if r: return r
    conn = get_db()
    conn.execute("DELETE FROM menu_choices WHERE guest_id=?", (guest_id,))
    conn.execute("DELETE FROM drink_choices WHERE guest_id=?", (guest_id,))
    conn.execute("DELETE FROM guests WHERE id=?", (guest_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/reset/<int:guest_id>", methods=["POST"])
def reset_guest(guest_id):
    r = admin_required()
    if r: return r
    conn = get_db()
    conn.execute("DELETE FROM menu_choices WHERE guest_id=?", (guest_id,))
    conn.execute("DELETE FROM drink_choices WHERE guest_id=?", (guest_id,))
    conn.execute("UPDATE guests SET attending_count=NULL, submitted=0 WHERE id=?", (guest_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/review/<int:guest_id>")
def review_guest(guest_id):
    r = admin_required()
    if r: return r
    conn = get_db()
    guest = conn.execute("SELECT * FROM guests WHERE id=?", (guest_id,)).fetchone()
    choices = conn.execute("SELECT * FROM menu_choices WHERE guest_id=? ORDER BY guest_number", (guest_id,)).fetchall()
    drinks = conn.execute("SELECT * FROM drink_choices WHERE guest_id=? ORDER BY guest_number, id", (guest_id,)).fetchall()
    conn.close()
    if not guest: abort(404)
    return render_template("review.html", guest=guest, choices=choices, drinks=drinks)

init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
