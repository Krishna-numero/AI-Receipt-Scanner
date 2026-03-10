import os
import re
import csv
import io
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
    Response,
    jsonify,
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from PIL import Image, ImageOps, ImageFilter
from sqlalchemy import text as sql_text

# EasyOCR-only setup
try:
    import easyocr
    EASYOCR_AVAILABLE = True
    _easyocr_reader = None
except Exception:
    EASYOCR_AVAILABLE = False
    _easyocr_reader = None

PDF2IMAGE_AVAILABLE = False
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{BASE_DIR / 'receipts.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

db = SQLAlchemy(app)


OCR_ENABLED = os.environ.get("OCR_ENABLED", "1").lower() in ("1", "true", "yes", "on")
EASYOCR_OK = EASYOCR_AVAILABLE and OCR_ENABLED
EASYOCR_ERR = "EasyOCR not installed" if not EASYOCR_OK else ""
OCR_SYNC_TOKEN = os.environ.get("OCR_SYNC_TOKEN")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)


class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(300))
    date = db.Column(db.String(50))
    total = db.Column(db.String(50))
    bill_category = db.Column(db.String(100))
    raw_text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def init_db():
    db.create_all()
    # lightweight migration: add original_filename column if missing
    try:
        cols = [row[1] for row in db.session.execute(sql_text("PRAGMA table_info(receipt)")).fetchall()]
        if "original_filename" not in cols:
            db.session.execute(sql_text("ALTER TABLE receipt ADD COLUMN original_filename VARCHAR(300)"))
            db.session.commit()
    except Exception:
        db.session.rollback()

    # backfill original_filename for existing receipts
    with app.app_context():
        updated = False
        for r in Receipt.query.filter((Receipt.original_filename == None) | (Receipt.original_filename == "")).all():
            r.original_filename = display_filename(r.filename)
            updated = True
        if updated:
            db.session.commit()

    if not EASYOCR_OK:
        with app.app_context():
            updated = False
            for r in Receipt.query.filter((Receipt.raw_text == None) | (Receipt.raw_text == "")).all():
                r.raw_text = f"[OCR UNAVAILABLE: {EASYOCR_ERR}]"
                updated = True
            if updated:
                db.session.commit()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def _check_sync_token(req):
    if not OCR_SYNC_TOKEN:
        return False
    auth = req.headers.get("Authorization", "")
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = req.headers.get("X-OCR-Token", "").strip()
    return bool(token) and token == OCR_SYNC_TOKEN


# helpers
@app.template_filter('display_date')
def display_date(value):
    """Format a date or datetime for UI (e.g. Feb 27, 2026)."""
    if not value:
        return ""
    if isinstance(value, str):
        # try common formats: ISO, YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
            try:
                dt = datetime.strptime(value, fmt)
                break
            except Exception:
                dt = None
        if dt is None:
            try:
                dt = datetime.fromisoformat(value)
            except Exception:
                return value
    else:
        dt = value
    try:
        return dt.strftime('%b %d, %Y')
    except Exception:
        return str(value)


@app.template_filter('display_filename')
def display_filename(value):
    if not value:
        return ""
    # strip collision suffix like "-d81cea31" before extension
    m = re.match(r"^(.*)-[0-9a-fA-F]{8}(\.[^.]+)$", value)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return value


def detect_bill_category(text: str) -> str:
    """Detect bill category from OCR text."""
    text_lower = text.lower()
    
    categories = {
        "Grocery": ["grocery", "supermarket", "market", "food", "walmart", "costco", "whole foods", "trader joe"],
        "Restaurant": ["restaurant", "cafe", "café", "pizza", "burger", "food court", "dining", "diner"],
        "Utilities": ["electricity", "water", "gas", "power", "utility", "electric bill", "water bill"],
        "Transportation": ["gas", "fuel", "station", "parking", "tolls", "transport", "airline", "hotel"],
        "Healthcare": ["pharmacy", "hospital", "clinic", "medical", "doctor", "dental", "health"],
        "Retail": ["store", "shopping", "mall", "boutique", "apparel", "clothing", "department store"],
        "Entertainment": ["movie", "cinema", "theatre", "ticket", "concert", "show", "event"],
        "Education": ["school", "university", "college", "tuition", "book", "course"],
    }
    
    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in text_lower:
                return category
    
    return "Other"


def _normalize_amount(amount: str) -> str:
    if not amount:
        return ""
    s = amount.strip()
    # keep digits, dot, comma and minus
    s = re.sub(r"[^0-9,.\-]", "", s)
    # If there are commas but no dots, treat comma as decimal separator (e.g. "12,34")
    if s.count(",") > 0 and s.count(".") == 0:
        if s.count(",") > 1:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    return s


def _pick_total_from_text(text: str) -> str:
    if not text:
        return ""

    # normalize common OCR currency noise
    text = text.replace("{", "₹").replace("}", "")

    total_keywords = [
        "total",
        "grand total",
        "amount due",
        "balance due",
        "payable",
        "total due",
    ]
    exclude_keywords = [
        "subtotal",
        "sub total",
        "sub-total",
        "round off",
        "rounding",
        "discount",
        "service charge",
        "tip",
        "change",
        "cgst",
        "sgst",
        "gst",
        "tax",
    ]
    amount_re = r"([0-9]{1,3}(?:,[0-9]{3})*[.,][0-9]{2})"
    currency_re = r"([€£$₹]\s*[0-9]{1,3}(?:,[0-9]{3})*[.,][0-9]{2})"

    # prefer numbers on lines that contain total keywords
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line_l = line.lower()
        line_l = line_l.replace("0", "o")
        if any(k in line_l for k in total_keywords) and not any(x in line_l for x in exclude_keywords):
            nums = re.findall(amount_re, line)
            if nums:
                def as_float(s: str) -> float:
                    try:
                        return float(_normalize_amount(s))
                    except Exception:
                        return 0.0
                return _normalize_amount(max(nums, key=as_float))
            # fallback to integer total on keyword lines (e.g. "TOTAL 1234")
            ints = re.findall(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,6})", line)
            if ints:
                return _normalize_amount(ints[-1])
            # if total keyword is on its own line, look ahead for next numeric-only line
            for j in range(i + 1, min(i + 3, len(lines))):
                nxt = lines[j].strip()
                if not nxt:
                    continue
                if any(c.isalpha() for c in nxt):
                    continue
                nums = re.findall(amount_re, nxt)
                if nums:
                    return _normalize_amount(nums[-1])
                ints = re.findall(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,6})", nxt)
                if ints:
                    return _normalize_amount(ints[-1])

    # regex across text for total keywords near amounts
    m2 = re.findall(
        r"(?:total|grand total|amount due|balance due|payable|total due)[^0-9]{0,20}([0-9]{1,3}(?:,[0-9]{3})*[.,][0-9]{2})",
        text,
        flags=re.IGNORECASE,
    )
    if m2:
        def as_float(s: str) -> float:
            try:
                return float(_normalize_amount(s))
            except Exception:
                return 0.0
        return _normalize_amount(max(m2, key=as_float))

    # fallback: if currency symbol present, pick the largest value
    m = re.findall(currency_re, text)
    if m:
        def as_float(s: str) -> float:
            try:
                return float(_normalize_amount(s))
            except Exception:
                return 0.0
        return _normalize_amount(max(m, key=as_float))

    # final fallback: choose the largest numeric value
    nums = re.findall(amount_re, text)
    if nums:
        def as_float(s: str) -> float:
            try:
                return float(_normalize_amount(s))
            except Exception:
                return 0.0
        return _normalize_amount(max(nums, key=as_float))

    return ""


def extract_fields(text: str):
    """Extract date, total, and bill_category from OCR text."""
    # date patterns
    date = ""
    date_patterns = [
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(\d{2}/\d{2}/\d{4})\b",
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
        r"\b(\d{1,2}[.\-]\d{1,2}[.\-]\d{2,4})\b",
        r"\b(\d{1,2}\s*[.\-]\s*\d{1,2}\s*[.\-]\s*\d{2,4})\b",
    ]
    for p in date_patterns:
        m = re.search(p, text)
        if m:
            date = m.group(1)
            break
    if not date:
        # try "Date" label followed by date on same or next line
        lines = [ln.strip() for ln in text.splitlines()]
        for i, line in enumerate(lines):
            if not line:
                continue
            if re.search(r"\bdate\b", line, flags=re.IGNORECASE):
                m = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", line)
                if m:
                    date = m.group(1)
                    break
                # look ahead for next non-empty line
                for j in range(i + 1, min(i + 4, len(lines))):
                    nxt = lines[j]
                    if not nxt:
                        continue
                    m2 = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", nxt)
                    if m2:
                        date = m2.group(1)
                        break
                if date:
                    break

    total = _pick_total_from_text(text)

    bill_category = detect_bill_category(text)

    return {"date": date, "total": total, "bill_category": bill_category, "raw_text": text}


def ocr_path(path: Path) -> str:
    """OCR an image file using EasyOCR (images only). Returns extracted text."""
    if not path.exists():
        return ""
    if not EASYOCR_OK:
        return ""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return ""

    try:
        img = Image.open(path)
    except Exception:
        return ""

    try:
        global _easyocr_reader
        if _easyocr_reader is None:
            _easyocr_reader = easyocr.Reader(["en"], gpu=False)

        img_rgb = img.convert("RGB")
        # prefer numpy array input
        try:
            arr = np.array(img_rgb)
            res = _easyocr_reader.readtext(arr)
        except Exception:
            try:
                res = _easyocr_reader.readtext(str(path))
            except Exception:
                res = _easyocr_reader.readtext(img_rgb)

        text = "\n".join([r[1] for r in res if r and len(r) > 1])
        if not text.strip():
            # try enhanced/resized fallback
            try:
                g = ImageOps.autocontrast(img_rgb.convert("L")).convert("RGB")
                w, h = g.size
                g2 = g.resize((min(2000, w * 2), min(2000, h * 2)), resample=Image.BICUBIC)
                arr2 = np.array(g2)
                res2 = _easyocr_reader.readtext(arr2)
                text = "\n".join([r[1] for r in res2 if r and len(r) > 1])
            except Exception:
                pass
        return text.strip()
    except Exception:
        return ""


def reprocess_all_receipts():
    """Re-run OCR and extraction for all receipts."""
    if not EASYOCR_OK:
        return 0
    updated = 0
    search_dirs = [Path(app.config["UPLOAD_FOLDER"]), Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Pictures"]
    with app.app_context():
        receipts = Receipt.query.order_by(Receipt.id).all()
        for r in receipts:
            raw = (r.raw_text or "").strip()
            use_text = ""
            if raw and not raw.startswith("["):
                use_text = raw
            else:
                found = None
                for d in search_dirs:
                    p = Path(d) / r.filename
                    if p.exists():
                        found = p
                        break
                if not found:
                    for dirpath, dirnames, filenames in os.walk(str(Path.home())):
                        if r.filename in filenames:
                            found = Path(dirpath) / r.filename
                            break
                if found:
                    use_text = ocr_path(found)

            if not use_text:
                continue

            fields = extract_fields(use_text)
            changed = False
            if r.raw_text != fields.get("raw_text"):
                r.raw_text = fields.get("raw_text")
                changed = True

            date_val = fields.get("date") or ""
            if not date_val:
                m = re.search(r"Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", use_text, flags=re.IGNORECASE)
                if m:
                    date_val = m.group(1)
            if (r.date or "") != (date_val or ""):
                r.date = date_val
                changed = True

            total_val = fields.get("total") or ""
            if not total_val:
                m = re.search(r"(?:total|amount due|amount|grand total)[:\s]*([0-9,]+[.,][0-9]{2})", use_text, flags=re.IGNORECASE)
                if m:
                    total_val = m.group(1)
                else:
                    m2 = re.findall(r"([0-9]+[.,][0-9]{2})", use_text)
                    if m2:
                        total_val = m2[-1]
            if (r.total or "") != (total_val or ""):
                r.total = total_val
                changed = True

            category_val = fields.get("bill_category") or ""
            if (r.bill_category or "") != (category_val or ""):
                r.bill_category = category_val
                changed = True

            if changed:
                updated += 1

        if updated:
            db.session.commit()
    return updated


@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    # show receipts in ascending order by id (older first) so the ID column is increasing
    receipts = Receipt.query.filter_by(user_id=user.id).order_by(Receipt.id.asc()).all()
    return render_template("index.html", receipts=receipts, user=user)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            flash("Username and password required", "error")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("Username already exists", "error")
            return redirect(url_for("register"))
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        flash("Registered — please log in", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid credentials", "error")
            return redirect(url_for("login"))
        session["user_id"] = user.id
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/upload", methods=["GET", "POST"])
def upload():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if request.method == "POST":
        f = request.files.get("file")
        if not f:
            flash("No file uploaded", "error")
            return redirect(url_for("upload"))
        original_filename = secure_filename(f.filename)
        filename = original_filename
        dest = Path(app.config["UPLOAD_FOLDER"]) / filename
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            filename = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
            dest = Path(app.config["UPLOAD_FOLDER"]) / filename
        f.save(dest)

        try:
            text = ocr_path(dest)
        except Exception as e:
            text = f"[OCR ERROR: {type(e).__name__}: {e}]"
            flash("OCR processing error: see receipt details for message.", "error")

        if not text.strip():
            if not EASYOCR_OK:
                if not OCR_ENABLED:
                    flash("OCR is disabled on the server. Set OCR_ENABLED=1 to enable.", "warning")
                else:
                    flash("No OCR engine available (EasyOCR). Install and restart.", "error")
            else:
                flash("OCR produced no text — try reprocessing or upload a clearer image.", "warning")

        fields = extract_fields(text)
        # allow manual date override from form
        manual_date = request.form.get("date")
        if manual_date:
            # store in ISO format (YYYY-MM-DD); browser date input gives this
            fields["date"] = manual_date

        receipt = Receipt(
            user_id=user.id,
            filename=filename,
            original_filename=original_filename,
            date=fields.get("date"),
            total=fields.get("total"),
            bill_category=fields.get("bill_category"),
            raw_text=fields.get("raw_text"),
        )
        db.session.add(receipt)
        db.session.commit()
        flash("Uploaded and processed", "success")
        return redirect(url_for("index"))
    return render_template("upload.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    user = current_user()
    if not user:
        return "Forbidden", 403
    r = Receipt.query.filter_by(user_id=user.id, filename=filename).first()
    if not r:
        return "Forbidden", 403
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/receipt/<int:rid>")
def receipt_view(rid):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    r = Receipt.query.get_or_404(rid)
    if r.user_id != user.id:
        return "Forbidden", 403
    return render_template("receipt.html", receipt=r)


@app.route("/export")
def export():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    
    try:
        start = request.args.get("start")
        end = request.args.get("end")
        q = Receipt.query.filter_by(user_id=user.id)
        if start:
            try:
                dt = datetime.fromisoformat(start)
                q = q.filter(Receipt.created_at >= dt)
            except ValueError:
                pass
        if end:
            try:
                dt = datetime.fromisoformat(end)
                # If the end value was supplied as a date (YYYY-MM-DD) from a date picker,
                # adjust to the end of that day so receipts on that date are included.
                if isinstance(end, str) and len(end) <= 10:
                    dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                q = q.filter(Receipt.created_at <= dt)
            except ValueError:
                pass
        items = q.order_by(Receipt.created_at.desc()).all()

        def parse_amount(s: str) -> float:
            if not s:
                return 0.0
            try:
                return float(_normalize_amount(s))
            except Exception:
                return 0.0

        total_sum = sum(parse_amount(r.total) for r in items)

        def stream_csv():
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["id", "date", "total", "bill_category", "filename", "created_at"])
            for r in items:
                writer.writerow([r.id, (r.date or ""), (r.total or ""), (r.bill_category or ""), (r.filename or ""), r.created_at.isoformat()])
            writer.writerow(["", "", f"{total_sum:.2f}", "TOTAL", "", ""])
            yield output.getvalue()

        response = Response(stream_csv(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=receipts.csv"
        return response
    
    except Exception as e:
        flash(f"Export error: {str(e)}", "error")
        return redirect(url_for("index"))


@app.route("/api/ocr_sync", methods=["POST"])
def ocr_sync():
    if not _check_sync_token(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    receipt_id = data.get("receipt_id")
    original_filename = (data.get("original_filename") or data.get("filename") or "").strip()

    user = None
    if username:
        user = User.query.filter_by(username=username).first()
        if not user:
            return jsonify({"ok": False, "error": "user_not_found"}), 404

    if receipt_id:
        r = Receipt.query.get(receipt_id)
        if not r:
            return jsonify({"ok": False, "error": "receipt_not_found"}), 404
        if user and r.user_id != user.id:
            return jsonify({"ok": False, "error": "forbidden"}), 403
    else:
        if not user:
            return jsonify({"ok": False, "error": "username_required"}), 400
        if not original_filename:
            return jsonify({"ok": False, "error": "original_filename_required"}), 400
        r = (Receipt.query
             .filter_by(user_id=user.id, original_filename=original_filename)
             .order_by(Receipt.created_at.desc())
             .first())
        if not r:
            r = (Receipt.query
                 .filter_by(user_id=user.id, filename=original_filename)
                 .order_by(Receipt.created_at.desc())
                 .first())
        if not r:
            return jsonify({"ok": False, "error": "receipt_not_found"}), 404

    # Update fields if provided
    for key in ("date", "total", "bill_category", "raw_text"):
        if key in data:
            setattr(r, key, data.get(key))
    db.session.commit()
    return jsonify({"ok": True, "receipt_id": r.id})


@app.route('/reprocess', methods=['POST'])
def reprocess_route():
    user = current_user()
    if not user:
        return redirect(url_for('login'))

    import threading

    def _worker():
        try:
            n = reprocess_all_receipts()
            print(f'Reprocessed {n} receipts')
        except Exception as e:
            print('Reprocess error', e)

    threading.Thread(target=_worker, daemon=True).start()
    flash('Reprocessing started in background — refresh later to see updates.', "info")
    return redirect(url_for('index'))


@app.route('/reprocess/<int:rid>', methods=['POST'])
def reprocess_receipt(rid):
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    if not EASYOCR_OK:
        flash("OCR is disabled on the server.", "warning")
        return redirect(url_for('index'))
    r = Receipt.query.get_or_404(rid)
    if r.user_id != user.id:
        return "Forbidden", 403

    # Re-run OCR and extraction for this single receipt
    try:
        search_dirs = [Path(app.config["UPLOAD_FOLDER"]), Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Pictures"]
        found = None
        for d in search_dirs:
            p = Path(d) / r.filename
            if p.exists():
                found = p
                break
        if not found:
            for dirpath, dirnames, filenames in os.walk(str(Path.home())):
                if r.filename in filenames:
                    found = Path(dirpath) / r.filename
                    break
        if found:
            text = ocr_path(found)
            if text:
                fields = extract_fields(text)
                r.raw_text = fields.get('raw_text')
                r.total = fields.get('total')
                r.bill_category = fields.get('bill_category')
                # Note: date is NOT overwritten; user already set it manually
                db.session.commit()
                flash(f'Receipt #{r.id} reprocessed successfully', "success")
            else:
                flash(f'OCR produced no text for receipt #{r.id}', "warning")
        else:
            flash(f'Receipt file not found', "error")
    except Exception as e:
        flash(f'Reprocess error: {e}', "error")
    return redirect(url_for('index'))


@app.route('/delete/<int:rid>', methods=['POST'])
def delete_receipt(rid):
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    r = Receipt.query.get_or_404(rid)
    if r.user_id != user.id:
        return "Forbidden", 403
    try:
        p = Path(app.config['UPLOAD_FOLDER']) / r.filename
        if p.exists():
            p.unlink()
    except Exception:
        pass
    db.session.delete(r)
    db.session.commit()
    flash('Receipt deleted', "success")
    return redirect(url_for('index'))


class ResponseStream:
    def write(self, data):
        return data


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
