import hashlib
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

# The SQLite database file lives in the database folder, next to this file.
DATABASE = os.path.join(app.root_path, "database", "database.db")

ACCOUNT_TYPES = ("seller", "buyer")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_NAME_LENGTH = 100
MAX_MESSAGE_LENGTH = 2000
# How long "Remember me" keeps someone logged in.
REMEMBER_ME_DURATION = timedelta(days=30)
# How long a password reset link works.
RESET_LINK_DURATION = timedelta(hours=1)
MAX_LISTING_TEXT_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 1000
# The largest id SQLite can store. Bigger numbers in a web address cause an error.
MAX_DATABASE_ID = 2**63 - 1
# Shown before every price.
CURRENCY_SYMBOL = "₦"

# ---------- Listing photos and videos ----------
# Uploaded files are saved in static/uploads/images and static/uploads/videos.
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
MAX_PHOTOS_PER_LISTING = 6
MAX_PHOTO_SIZE = 5 * 1024 * 1024    # 5 MB per photo
MAX_VIDEO_SIZE = 50 * 1024 * 1024   # 50 MB for the one video
# The file extensions each upload box accepts. The file's contents are checked
# too (see detect_file_type), so renaming a file does not get it past the check.
PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXTENSIONS = {"mp4", "webm"}
# Saved files are always named like 3f9a...c2.jpg: 32 random hex characters
# and one of these extensions. Nothing else is ever written to the uploads folder.
SAFE_MEDIA_FILENAME = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp|mp4|webm)")


def load_secret_key():
    """Return the key Flask uses to sign session cookies.

    The key comes from the SECRET_KEY environment variable if it is set.
    Otherwise a random key is created once and saved in the instance folder,
    so users stay logged in when the server restarts.
    """
    key = os.environ.get("SECRET_KEY")
    if key:
        return key

    key_file = os.path.join(app.instance_path, "secret_key.txt")
    if os.path.exists(key_file):
        with open(key_file) as file:
            return file.read().strip()

    os.makedirs(app.instance_path, exist_ok=True)
    key = secrets.token_hex(32)
    with open(key_file, "w") as file:
        file.write(key)
    return key


app.secret_key = load_secret_key()
# Browsers will not send the login cookie with forms posted from other websites.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Used only when someone ticks "Remember me". Otherwise the login ends
# when the browser is closed.
app.permanent_session_lifetime = REMEMBER_ME_DURATION
# The largest form Flask will accept: every photo and the video at their size
# limits, plus room for the text fields. Anything bigger is refused (error 413).
app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTOS_PER_LISTING * MAX_PHOTO_SIZE + MAX_VIDEO_SIZE + 1024 * 1024

for folder in ("images", "videos"):
    os.makedirs(os.path.join(UPLOAD_FOLDER, folder), exist_ok=True)


# ---------- Database ----------

def get_db():
    """Open one database connection per request and reuse it."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        # Lets us read columns by name, like row["email"].
        g.db.row_factory = sqlite3.Row
        # SQLite only enforces links between tables when this is switched on.
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    """Close the database connection when the request finishes."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def add_missing_columns(db, table, columns):
    """Add new columns to an existing table, leaving its rows untouched.

    CREATE TABLE IF NOT EXISTS does nothing when a table already exists,
    so columns added to the project later are added here instead.
    """
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db():
    """Create the tables. IF NOT EXISTS keeps any existing data safe."""
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            account_type TEXT NOT NULL CHECK (account_type IN ('seller', 'buyer')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Added with the new sign-up forms. Accounts created before then have
    # no value in these columns, which is fine.
    add_missing_columns(db, "users", {
        "business_name": "TEXT",
        "business_description": "TEXT",
        "terms_accepted_at": "TEXT",
    })
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL REFERENCES users (id),
            waste_type TEXT NOT NULL,
            description TEXT NOT NULL,
            quantity_kg REAL NOT NULL CHECK (quantity_kg > 0),
            price_per_kg REAL NOT NULL CHECK (price_per_kg > 0),
            location TEXT NOT NULL,
            contact_phone TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # One row per "Forgot password" request. Only a hash of each link's secret
    # is saved, so the database alone cannot be used to reset a password.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Photos and videos for each listing. The files themselves are in
    # static/uploads; this table records which file belongs to which listing.
    # ON DELETE CASCADE removes these rows when their listing is deleted.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS listing_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES listings (id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('image', 'video')),
            filename TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS listing_media_listing ON listing_media (listing_id)")
    # Messages sent with the Contact form on the homepage.
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.commit()


with app.app_context():
    init_db()


# ---------- Template helpers ----------

def csrf_token():
    """Return this visitor's secret form token, creating it if needed."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.template_filter("money")
def format_money(value):
    """Show a price in Naira, like ₦15,000 or ₦12.50 (kobo only when there are any)."""
    if value == int(value):
        return f"{CURRENCY_SYMBOL}{value:,.0f}"
    return f"{CURRENCY_SYMBOL}{value:,.2f}"


@app.template_filter("date")
def format_date(value):
    """Show a stored date like "2026-11-04 08:04:46" as "4 Nov 2026"."""
    day = datetime.strptime(value[:10], "%Y-%m-%d")
    return f"{day.day} {day:%b %Y}"


@app.template_filter("amount")
def format_amount(value):
    """Show a quantity without extra zeros, like 1,250 or 12.5."""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


# ---------- Login helpers ----------

def password_fingerprint(password_hash):
    """A short code that changes whenever the user's password changes.

    It is saved in the session at login. After a password reset the codes no
    longer match, so every browser still logged in to that account is logged out.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


@app.before_request
def load_logged_in_user():
    """Make the logged-in user available as g.user on every request."""
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return

    user = get_db().execute(
        """
        SELECT id, full_name, email, phone, account_type, business_name,
               business_description, password_hash
        FROM users WHERE id = ?
        """,
        (user_id,),
    ).fetchone()

    if user is None or session.get("password_fingerprint") != password_fingerprint(user["password_hash"]):
        session.clear()
        return

    # Everything except the password hash, which pages never need.
    g.user = {key: user[key] for key in user.keys() if key != "password_hash"}


@app.before_request
def check_csrf_token():
    """Reject submitted forms that did not come from one of our own pages."""
    if request.method != "POST":
        return None

    expected = session.get("csrf_token", "")
    submitted = request.form.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected.encode(), submitted.encode()):
        flash("That form expired, so nothing was saved. Please try again.", "error")
        return redirect(url_for("index"))
    return None


@app.after_request
def stop_caching_private_pages(response):
    """Stop the browser's Back button showing a dashboard after logout."""
    if g.get("user") and request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


def login_required(view):
    """Send visitors to the login page unless they are logged in."""
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(**kwargs)
    return wrapped_view


def seller_required(view):
    """Allow logged-in sellers only. Everyone else is turned away."""
    @wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if g.user["account_type"] != "seller":
            abort(403, "Only seller accounts can add, edit or delete listings.")
        return view(**kwargs)
    return wrapped_view


def log_in(user_id, password_hash, remember=False):
    """Start a fresh session for this user.

    With remember=True the login lasts REMEMBER_ME_DURATION. Otherwise it
    ends when the browser is closed.
    """
    session.clear()
    session.permanent = remember
    session["user_id"] = user_id
    session["password_fingerprint"] = password_fingerprint(password_hash)


def dashboard_url(account_type):
    """Return the dashboard address for a seller or a buyer."""
    return url_for(f"{account_type}_dashboard")


def utc_now():
    """The current time as text in the same format SQLite's CURRENT_TIMESTAMP uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_valid_phone(phone):
    """Spaces, dashes and brackets are fine, as long as there are 7-15 digits."""
    phone_digits = re.sub(r"[\s()-]", "", phone)
    return re.fullmatch(r"\+?[0-9]{7,15}", phone_digits) is not None


def is_valid_email(email):
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None and len(email) <= 254


def validate_new_password(form, errors):
    """Check the password and confirm_password fields, adding any errors."""
    if len(form["password"]) < MIN_PASSWORD_LENGTH:
        errors["password"] = f"Use at least {MIN_PASSWORD_LENGTH} characters."
    elif len(form["password"]) > MAX_PASSWORD_LENGTH:
        errors["password"] = f"Use {MAX_PASSWORD_LENGTH} characters or fewer."

    if not form["confirm_password"]:
        errors["confirm_password"] = "Type your password again to confirm it."
    elif form["confirm_password"] != form["password"]:
        errors["confirm_password"] = "The passwords do not match."


def read_registration_form(role):
    """Collect the sign-up fields, tidying the spacing."""
    form = {
        "full_name": " ".join(request.form.get("full_name", "").split()),
        # Emails are saved in lowercase so Ada@x.com and ada@x.com count as one.
        "email": request.form.get("email", "").strip().lower(),
        "phone": request.form.get("phone", "").strip(),
        "password": request.form.get("password", ""),
        "confirm_password": request.form.get("confirm_password", ""),
        "terms": request.form.get("terms") == "yes",
    }
    # Sellers must give a business name; for buyers it is an optional company name.
    form["business_name"] = " ".join(request.form.get("business_name", "").split())
    if role == "seller":
        form["business_description"] = (
            request.form.get("business_description", "").replace("\r\n", "\n").strip()
        )
    return form


def validate_registration(form, role):
    """Return a dictionary of error messages, one for each invalid field."""
    errors = {}

    if not form["full_name"]:
        errors["full_name"] = "Enter your full name."
    elif len(form["full_name"]) > MAX_NAME_LENGTH:
        errors["full_name"] = f"Full name must be {MAX_NAME_LENGTH} characters or fewer."

    if role == "seller" and not form["business_name"]:
        errors["business_name"] = "Enter your business or farm name."
    elif len(form["business_name"]) > MAX_NAME_LENGTH:
        errors["business_name"] = f"Business name must be {MAX_NAME_LENGTH} characters or fewer."

    if not is_valid_email(form["email"]):
        errors["email"] = "Enter a valid email address, like name@example.com."

    if not is_valid_phone(form["phone"]):
        errors["phone"] = "Enter a valid phone number, like +234 801 234 5678."

    validate_new_password(form, errors)

    if role == "seller":
        if not form["business_description"]:
            errors["business_description"] = "Tell buyers what your business does and what waste you produce."
        elif len(form["business_description"]) > MAX_DESCRIPTION_LENGTH:
            errors["business_description"] = f"Use {MAX_DESCRIPTION_LENGTH} characters or fewer."

    if not form["terms"]:
        errors["terms"] = "You need to accept the terms and conditions to create an account."

    return errors


# ---------- Listing helpers ----------

def parse_amount(text):
    """Turn text like "1500" or "12.50" into a number, or None if it is invalid.

    Only plain digits are accepted, so "nan", "inf", "-5" and "1e9" are refused.
    """
    if re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,2})?", text):
        return float(text)
    return None


def read_listing_form():
    """Collect the listing fields from the submitted form, tidying the spacing."""
    return {
        "waste_type": " ".join(request.form.get("waste_type", "").split()),
        "description": request.form.get("description", "").replace("\r\n", "\n").strip(),
        "quantity_kg": request.form.get("quantity_kg", "").strip(),
        "price_per_kg": request.form.get("price_per_kg", "").strip(),
        "location": " ".join(request.form.get("location", "").split()),
        "contact_phone": request.form.get("contact_phone", "").strip(),
    }


def validate_listing(form):
    """Return a dictionary of error messages, one for each invalid field."""
    errors = {}

    if not form["waste_type"]:
        errors["waste_type"] = "Enter the waste name or type, like Rice husks."
    elif len(form["waste_type"]) > MAX_LISTING_TEXT_LENGTH:
        errors["waste_type"] = f"Use {MAX_LISTING_TEXT_LENGTH} characters or fewer."

    if not form["description"]:
        errors["description"] = "Describe the waste, like its condition and packaging."
    elif len(form["description"]) > MAX_DESCRIPTION_LENGTH:
        errors["description"] = f"Use {MAX_DESCRIPTION_LENGTH} characters or fewer."

    quantity = parse_amount(form["quantity_kg"])
    if quantity is None or quantity <= 0:
        errors["quantity_kg"] = "Enter a quantity above 0, with up to 2 decimal places."

    price = parse_amount(form["price_per_kg"])
    if price is None or price <= 0:
        errors["price_per_kg"] = "Enter a price above 0, with up to 2 decimal places."

    if not form["location"]:
        errors["location"] = "Enter the town or region where the waste is."
    elif len(form["location"]) > MAX_LISTING_TEXT_LENGTH:
        errors["location"] = f"Use {MAX_LISTING_TEXT_LENGTH} characters or fewer."

    if not is_valid_phone(form["contact_phone"]):
        errors["contact_phone"] = "Enter a valid phone number, like +234 801 234 5678."

    return errors


# ---------- Listing media helpers ----------

def detect_file_type(upload):
    """Look at the first bytes of a file to see what it really is.

    Returns "jpg", "png", "webp", "mp4", "webm" or None. Every one of these
    formats starts with a known pattern of bytes, so a program or web page
    renamed to photo.jpg is still recognised as "not a photo".
    """
    head = upload.stream.read(64)
    upload.stream.seek(0)
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    # MP4 files have "ftyp" at byte 4. "qt" is Apple's .mov format, which
    # most browsers cannot play, so it is refused.
    if head[4:8] == b"ftyp" and head[8:10] != b"qt":
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in head:
        return "webm"
    return None


def file_size(upload):
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)
    return size


def read_media_uploads():
    """Return the chosen photo files and video file, skipping empty upload boxes."""
    photos = [f for f in request.files.getlist("photos") if f and f.filename]
    video = request.files.get("video")
    if not (video and video.filename):
        video = None
    return photos, video


def check_upload(upload, allowed_extensions, allowed_types, max_size, label):
    """Check one file. Returns (file_type, size, None) or (None, None, error message)."""
    size = file_size(upload)
    if size == 0:
        return None, None, f"{label} is empty."
    extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    file_type = detect_file_type(upload)
    if extension not in allowed_extensions or file_type not in allowed_types:
        return None, None, f"{label} is not a supported file type."
    if size > max_size:
        return None, None, f"{label} is larger than {max_size // (1024 * 1024)} MB."
    return file_type, size, None


def validate_media(photos, video, photos_kept=0):
    """Check the uploaded files. Returns (checked files, errors).

    photos_kept is how many photos the listing already has and keeps, so the
    total stays within MAX_PHOTOS_PER_LISTING.
    """
    checked, errors, photo_problems = [], {}, []

    if photos_kept + len(photos) > MAX_PHOTOS_PER_LISTING:
        allowed = MAX_PHOTOS_PER_LISTING - photos_kept
        errors["photos"] = (
            f"A listing can have up to {MAX_PHOTOS_PER_LISTING} photos. "
            + (f"You can add {allowed} more." if allowed > 0 else "Remove a photo to add another.")
        )
    else:
        for number, upload in enumerate(photos, start=1):
            file_type, size, problem = check_upload(
                upload, PHOTO_EXTENSIONS, {"jpg", "png", "webp"}, MAX_PHOTO_SIZE, f"Photo {number}"
            )
            if problem:
                photo_problems.append(problem)
            else:
                checked.append(("image", upload, file_type, size))
        if photo_problems:
            errors["photos"] = " ".join(photo_problems) + " Use JPG, PNG or WEBP photos up to 5 MB each."

    if video:
        file_type, size, problem = check_upload(
            video, VIDEO_EXTENSIONS, {"mp4", "webm"}, MAX_VIDEO_SIZE, "The video"
        )
        if problem:
            errors["video"] = problem + " Use an MP4 or WEBM video up to 50 MB."
        else:
            checked.append(("video", video, file_type, size))

    return checked, errors


def media_file_path(kind, filename):
    """The full path of a saved upload, or None if the name is not one we created."""
    if not SAFE_MEDIA_FILENAME.fullmatch(filename or ""):
        return None
    return os.path.join(UPLOAD_FOLDER, "images" if kind == "image" else "videos", filename)


def save_media_files(checked):
    """Save checked uploads under new random names. Returns (kind, filename, size) for each."""
    saved = []
    for kind, upload, file_type, size in checked:
        # A new random name, never the uploader's own filename.
        filename = f"{secrets.token_hex(16)}.{file_type}"
        upload.save(media_file_path(kind, filename))
        saved.append((kind, filename, size))
    return saved


def delete_media_files(media):
    """Delete saved upload files from disk. Files already missing are skipped."""
    for item in media:
        path = media_file_path(item["kind"], item["filename"])
        if path:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def insert_media_rows(db, listing_id, saved, first_position=0):
    for offset, (kind, filename, size) in enumerate(saved):
        db.execute(
            """
            INSERT INTO listing_media (listing_id, kind, filename, size_bytes, position)
            VALUES (?, ?, ?, ?, ?)
            """,
            (listing_id, kind, filename, size, first_position + offset),
        )


def get_listing_media(listing_id):
    """Return a listing's photos and video, leaving out any file missing from disk."""
    rows = get_db().execute(
        "SELECT * FROM listing_media WHERE listing_id = ? ORDER BY position, id",
        (listing_id,),
    ).fetchall()
    present = [row for row in rows if os.path.exists(media_file_path(row["kind"], row["filename"]) or "")]
    return {
        "images": [row for row in present if row["kind"] == "image"],
        "video": next((row for row in present if row["kind"] == "video"), None),
    }


def media_url(kind, filename):
    folder = "images" if kind == "image" else "videos"
    return url_for("static", filename=f"uploads/{folder}/{filename}")


app.jinja_env.globals["media_url"] = media_url
app.jinja_env.globals["MAX_PHOTOS_PER_LISTING"] = MAX_PHOTOS_PER_LISTING


# SQL that adds each listing's cover photo and media counts to a listings query.
MEDIA_SUMMARY_SQL = """
    (SELECT filename FROM listing_media
     WHERE listing_id = listings.id AND kind = 'image'
     ORDER BY position, id LIMIT 1) AS cover_photo,
    (SELECT COUNT(*) FROM listing_media
     WHERE listing_id = listings.id AND kind = 'image') AS photo_count,
    (SELECT COUNT(*) FROM listing_media
     WHERE listing_id = listings.id AND kind = 'video') AS video_count
"""


def get_listing(listing_id):
    """Return one listing and its seller's name, or show a 404 page."""
    listing = None
    if listing_id <= MAX_DATABASE_ID:
        listing = get_db().execute(
            """
            SELECT listings.*, users.full_name AS seller_name, users.business_name AS seller_business,
                   users.business_description AS seller_about, users.created_at AS seller_since
            FROM listings
            JOIN users ON users.id = listings.seller_id
            WHERE listings.id = ?
            """,
            (listing_id,),
        ).fetchone()

    if listing is None:
        abort(404, "This listing does not exist. It may have been deleted.")
    return listing


def get_own_listing(listing_id):
    """Return a listing only if it belongs to the logged-in seller."""
    listing = get_listing(listing_id)
    if listing["seller_id"] != g.user["id"]:
        abort(403, "You can only edit or delete your own listings.")
    return listing


def render_seller_dashboard(form, errors):
    """Show the seller's listings beside the form for adding a new one."""
    listings = get_db().execute(
        f"""
        SELECT listings.*, {MEDIA_SUMMARY_SQL}
        FROM listings WHERE seller_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template(
        "seller_dashboard.html", listings=listings, form=form, errors=errors,
        media={"images": [], "video": None},
    )


def search_listings(filters):
    """Return the listings matching the marketplace filters, and any filter errors."""
    conditions = []
    values = []
    errors = {}

    if filters["search"]:
        # A backslash stops % and _ in the search acting as SQL wildcards.
        search = re.sub(r"([\\%_])", r"\\\1", filters["search"])
        conditions.append("listings.waste_type LIKE ? ESCAPE '\\'")
        values.append(f"%{search}%")

    if filters["location"]:
        conditions.append("listings.location = ? COLLATE NOCASE")
        values.append(filters["location"])

    min_price = parse_amount(filters["min_price"])
    if filters["min_price"] and min_price is None:
        errors["min_price"] = "Enter a number, like 10 or 12.50."
    elif min_price is not None:
        conditions.append("listings.price_per_kg >= ?")
        values.append(min_price)

    max_price = parse_amount(filters["max_price"])
    if filters["max_price"] and max_price is None:
        errors["max_price"] = "Enter a number, like 50 or 62.50."
    elif max_price is not None:
        conditions.append("listings.price_per_kg <= ?")
        values.append(max_price)

    if min_price is not None and max_price is not None and min_price > max_price:
        errors["max_price"] = "This must be higher than the minimum price."

    sql = f"""
        SELECT listings.*, users.full_name AS seller_name, users.business_name AS seller_business,
               {MEDIA_SUMMARY_SQL}
        FROM listings
        JOIN users ON users.id = listings.seller_id
    """
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY listings.created_at DESC, listings.id DESC"

    return get_db().execute(sql, values).fetchall(), errors


# ---------- Error pages ----------

@app.errorhandler(403)
@app.errorhandler(404)
def show_error_page(error):
    return render_template("error.html", error=error), error.code


@app.errorhandler(413)
def upload_too_large(error):
    error.description = (
        "The files you chose are too large to upload together. Photos can be up to "
        "5 MB each and a video up to 50 MB. Please go back and choose smaller files."
    )
    return render_template("error.html", error=error), 413


@app.after_request
def protect_uploaded_files(response):
    """Uploaded files are only ever shown as pictures and videos.

    nosniff stops browsers guessing a different file type, and the
    Content-Security-Policy stops any script running from these addresses.
    """
    if request.path.startswith("/static/uploads/"):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


# ---------- Pages ----------

@app.route("/")
def index():
    return render_template("index.html", contact={}, contact_errors={})


@app.route("/contact", methods=["POST"])
def contact():
    form = {
        "name": " ".join(request.form.get("name", "").split()),
        "email": request.form.get("email", "").strip().lower(),
        "message": request.form.get("message", "").replace("\r\n", "\n").strip(),
    }
    errors = {}
    if not form["name"]:
        errors["name"] = "Enter your name."
    elif len(form["name"]) > MAX_NAME_LENGTH:
        errors["name"] = f"Use {MAX_NAME_LENGTH} characters or fewer."
    if not is_valid_email(form["email"]):
        errors["email"] = "Enter a valid email address, so we can reply."
    if not form["message"]:
        errors["message"] = "Write your message."
    elif len(form["message"]) > MAX_MESSAGE_LENGTH:
        errors["message"] = f"Use {MAX_MESSAGE_LENGTH} characters or fewer."

    if errors:
        return render_template("index.html", contact=form, contact_errors=errors), 400

    db = get_db()
    db.execute(
        "INSERT INTO contact_messages (name, email, message) VALUES (?, ?, ?)",
        (form["name"], form["email"], form["message"]),
    )
    db.commit()
    flash("Thank you! Your message has been sent. We will reply by email.", "success")
    return redirect(url_for("index", _anchor="contact"))


@app.route("/register")
def register():
    """Sign up, step 1: choose Buyer or Seller."""
    if g.user:
        return redirect(dashboard_url(g.user["account_type"]))

    # Older links used /register?type=seller. Send them straight to that form.
    if request.args.get("type") in ACCOUNT_TYPES:
        return redirect(url_for("register_account", role=request.args["type"]))

    return render_template("choose_role.html")


@app.route("/register/<role>", methods=["GET", "POST"])
def register_account(role):
    """Sign up, step 2: the buyer or seller form.

    The role comes from the web address, never from a form field, so it
    stays the same from the role cards right through to the new account.
    """
    if role not in ACCOUNT_TYPES:
        abort(404, "That account type does not exist. Choose Buyer or Seller.")
    if g.user:
        return redirect(dashboard_url(g.user["account_type"]))

    if request.method == "GET":
        return render_template("register.html", role=role, form={}, errors={})

    form = read_registration_form(role)
    errors = validate_registration(form, role)

    db = get_db()
    if "email" not in errors:
        existing = db.execute(
            "SELECT id FROM users WHERE email = ?", (form["email"],)
        ).fetchone()
        if existing:
            errors["email"] = "This email is already registered. Try logging in instead."

    if not errors:
        password_hash = generate_password_hash(form["password"])
        try:
            cursor = db.execute(
                """
                INSERT INTO users (full_name, email, phone, password_hash, account_type,
                                   business_name, business_description, terms_accepted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    form["full_name"],
                    form["email"],
                    form["phone"],
                    password_hash,
                    role,
                    # An empty optional company name is saved as no value.
                    form["business_name"] or None,
                    form.get("business_description"),
                    utc_now(),
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            # Two people submitted the same email at the same moment.
            errors["email"] = "This email is already registered. Try logging in instead."

    if errors:
        return render_template("register.html", role=role, form=form, errors=errors), 400

    log_in(cursor.lastrowid, password_hash)
    flash("Welcome to HeyCileng! Your account is ready.", "success")
    return redirect(dashboard_url(role))


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(dashboard_url(g.user["account_type"]))

    if request.method == "GET":
        return render_template("login.html", email="", remember=False, error=None)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    remember = request.form.get("remember") == "yes"
    user = get_db().execute(
        "SELECT id, password_hash, account_type FROM users WHERE email = ?", (email,)
    ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        # The same message for both cases, so nobody can use this form to
        # find out which email addresses have accounts.
        error = "Incorrect email or password. Please check your details and try again."
        return render_template("login.html", email=email, remember=remember, error=error), 401

    log_in(user["id"], user["password_hash"], remember)
    return redirect(dashboard_url(user["account_type"]))


def hash_reset_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def find_reset_request(token):
    """Return the unused, unexpired reset request for this link, or None."""
    return get_db().execute(
        """
        SELECT password_resets.id, password_resets.user_id, users.email
        FROM password_resets
        JOIN users ON users.id = password_resets.user_id
        WHERE token_hash = ? AND expires_at > ?
        """,
        (hash_reset_token(token), utc_now()),
    ).fetchone()


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if g.user:
        return redirect(dashboard_url(g.user["account_type"]))

    if request.method == "GET":
        return render_template("forgot_password.html", email="", error=None, sent=False)

    email = request.form.get("email", "").strip().lower()
    if not is_valid_email(email):
        error = "Enter the email address you signed up with."
        return render_template("forgot_password.html", email=email, error=error, sent=False), 400

    db = get_db()
    user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if user:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + RESET_LINK_DURATION).strftime("%Y-%m-%d %H:%M:%S")
        # Only the newest link works, so older ones are removed.
        db.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
        db.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user["id"], hash_reset_token(token), expires_at),
        )
        db.commit()

        link = url_for("reset_password", token=token, _external=True)
        # Email sending is not set up yet. While developing, the link is printed
        # in the terminal instead. Replace this with an email before launch.
        if app.debug:
            print(f"\n*** Password reset link for {email}:\n{link}\n", flush=True)

    # The same answer whether or not the account exists, so this page cannot be
    # used to find out who has an account.
    return render_template("forgot_password.html", email=email, error=None, sent=True)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    reset = find_reset_request(token)
    if reset is None:
        return render_template("reset_password.html", valid=False, errors={}), 404

    if request.method == "GET":
        return render_template("reset_password.html", valid=True, errors={})

    form = {
        "password": request.form.get("password", ""),
        "confirm_password": request.form.get("confirm_password", ""),
    }
    errors = {}
    validate_new_password(form, errors)
    if errors:
        return render_template("reset_password.html", valid=True, errors=errors), 400

    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(form["password"]), reset["user_id"]),
    )
    # The link only works once.
    db.execute("DELETE FROM password_resets WHERE user_id = ?", (reset["user_id"],))
    db.commit()

    # Changing the password also logs this account out everywhere else.
    session.clear()
    flash("Your password has been changed. Please log in with your new password.", "success")
    return redirect(url_for("login"))


# Logout is POST-only so that a link on another website cannot log someone out.
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/seller/dashboard")
@login_required
def seller_dashboard():
    if g.user["account_type"] != "seller":
        return redirect(dashboard_url(g.user["account_type"]))
    # The contact phone starts as the seller's own number, which they can change.
    return render_seller_dashboard({"contact_phone": g.user["phone"]}, {})


@app.route("/buyer/dashboard")
@login_required
def buyer_dashboard():
    if g.user["account_type"] != "buyer":
        return redirect(dashboard_url(g.user["account_type"]))
    return render_template("buyer_dashboard.html")


REUPLOAD_NOTE = "For your security, browsers forget chosen files when a form has errors. Please choose your photos and video again."


@app.route("/listings/new", methods=["GET", "POST"])
@seller_required
def create_listing():
    if request.method == "GET":
        return redirect(url_for("seller_dashboard") + "#add-listing")

    form = read_listing_form()
    photos, video = read_media_uploads()
    errors = validate_listing(form)
    checked, media_errors = validate_media(photos, video)
    errors.update(media_errors)
    if errors:
        if (photos or video) and not media_errors:
            errors["photos"] = REUPLOAD_NOTE
        flash("Your listing was not saved. Please fix the highlighted fields.", "error")
        return render_seller_dashboard(form, errors), 400

    db = get_db()
    saved = save_media_files(checked)
    try:
        cursor = db.execute(
            """
            INSERT INTO listings
                (seller_id, waste_type, description, quantity_kg, price_per_kg, location, contact_phone)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                # Always the logged-in user, never a value sent by the browser.
                g.user["id"],
                form["waste_type"],
                form["description"],
                float(form["quantity_kg"]),
                float(form["price_per_kg"]),
                form["location"],
                form["contact_phone"],
            ),
        )
        insert_media_rows(db, cursor.lastrowid, saved)
        db.commit()
    except Exception:
        # Nothing was saved in the database, so remove the files too.
        db.rollback()
        delete_media_files([{"kind": kind, "filename": name} for kind, name, _ in saved])
        raise

    flash("Your listing has been added to the marketplace.", "success")
    # "#top" opens the dashboard at the top, where the message and the updated
    # totals are. Without it the browser would keep the form's "#add-listing".
    return redirect(url_for("seller_dashboard", _anchor="top"))


def read_removed_media_ids():
    """The ids of the existing photos and video the seller ticked "Remove" on."""
    return {int(value) for value in request.form.getlist("remove_media") if value.isdigit()}


@app.route("/listings/<int:listing_id>/edit", methods=["GET", "POST"])
@seller_required
def edit_listing(listing_id):
    # get_own_listing stops anyone except this listing's seller, so nobody
    # else can add, replace or remove its photos and video.
    listing = get_own_listing(listing_id)

    if request.method == "GET":
        form = dict(listing)
        form["quantity_kg"] = f"{listing['quantity_kg']:.2f}"
        form["price_per_kg"] = f"{listing['price_per_kg']:.2f}"
        return render_template("edit_listing.html", listing=listing, form=form, errors={},
                               media=get_listing_media(listing_id), removing=set())

    form = read_listing_form()
    photos, video = read_media_uploads()
    removing = read_removed_media_ids()

    db = get_db()
    # Only this listing's own media can be removed. Ids belonging to other
    # listings are simply not in this list.
    current = db.execute(
        "SELECT * FROM listing_media WHERE listing_id = ? ORDER BY position, id", (listing_id,)
    ).fetchall()
    to_remove = [
        row for row in current
        if row["id"] in removing
        # A new video replaces the current one.
        or (row["kind"] == "video" and video is not None)
        # Rows whose file has gone missing are tidied up.
        or not os.path.exists(media_file_path(row["kind"], row["filename"]) or "")
    ]
    removed_ids = {row["id"] for row in to_remove}
    photos_kept = sum(1 for row in current if row["kind"] == "image" and row["id"] not in removed_ids)

    errors = validate_listing(form)
    checked, media_errors = validate_media(photos, video, photos_kept)
    errors.update(media_errors)
    if errors:
        if (photos or video) and not media_errors:
            errors["photos"] = REUPLOAD_NOTE
        flash("Your changes were not saved. Please fix the highlighted fields.", "error")
        return render_template("edit_listing.html", listing=listing, form=form, errors=errors,
                               media=get_listing_media(listing_id), removing=removing), 400

    saved = save_media_files(checked)
    try:
        db.execute(
            """
            UPDATE listings
            SET waste_type = ?, description = ?, quantity_kg = ?, price_per_kg = ?,
                location = ?, contact_phone = ?
            WHERE id = ? AND seller_id = ?
            """,
            (
                form["waste_type"],
                form["description"],
                float(form["quantity_kg"]),
                float(form["price_per_kg"]),
                form["location"],
                form["contact_phone"],
                listing_id,
                g.user["id"],
            ),
        )
        for row in to_remove:
            db.execute("DELETE FROM listing_media WHERE id = ? AND listing_id = ?", (row["id"], listing_id))
        next_position = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM listing_media WHERE listing_id = ?", (listing_id,)
        ).fetchone()[0]
        insert_media_rows(db, listing_id, saved, next_position)
        db.commit()
    except Exception:
        db.rollback()
        delete_media_files([{"kind": kind, "filename": name} for kind, name, _ in saved])
        raise

    # Only delete the old files once the database change is safely saved.
    delete_media_files(to_remove)
    flash("Your listing has been updated.", "success")
    return redirect(url_for("seller_dashboard"))


@app.route("/listings/<int:listing_id>/delete", methods=["POST"])
@seller_required
def delete_listing(listing_id):
    get_own_listing(listing_id)

    db = get_db()
    media = db.execute("SELECT kind, filename FROM listing_media WHERE listing_id = ?", (listing_id,)).fetchall()
    # The listing's media rows are removed with it (ON DELETE CASCADE).
    db.execute(
        "DELETE FROM listings WHERE id = ? AND seller_id = ?",
        (listing_id, g.user["id"]),
    )
    db.commit()
    delete_media_files(media)
    flash("Your listing has been deleted.", "success")
    return redirect(url_for("seller_dashboard"))


# The marketplace needs a login because listings show sellers' phone numbers.
@app.route("/marketplace")
@login_required
def marketplace():
    filters = {
        "search": " ".join(request.args.get("search", "").split())[:MAX_LISTING_TEXT_LENGTH],
        "location": request.args.get("location", "").strip(),
        "min_price": request.args.get("min_price", "").strip(),
        "max_price": request.args.get("max_price", "").strip(),
    }
    listings, errors = search_listings(filters)
    locations = get_db().execute(
        """
        SELECT location FROM listings
        GROUP BY location COLLATE NOCASE
        ORDER BY location COLLATE NOCASE
        """
    ).fetchall()
    return render_template(
        "marketplace.html",
        listings=listings,
        locations=locations,
        filters=filters,
        errors=errors,
    )


@app.route("/listings/<int:listing_id>")
@login_required
def listing_detail(listing_id):
    listing = get_listing(listing_id)
    return render_template("listing_detail.html", listing=listing, media=get_listing_media(listing_id))


if __name__ == "__main__":
    # debug=True reloads the server when you save a file. Local development only.
    app.run(debug=True)
