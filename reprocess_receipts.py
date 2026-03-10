from pathlib import Path
import os
import re
from app import app, Receipt, db, extract_fields, _pick_total_from_text, _normalize_amount, EASYOCR_OK
from PIL import Image
import easyocr


SEARCH_DIRS = [Path(app.config['UPLOAD_FOLDER']), Path.home() / 'Downloads', Path.home() / 'Desktop', Path.home() / 'Documents', Path.home() / 'Pictures']


def find_file(filename):
    for d in SEARCH_DIRS:
        p = d / filename
        if p.exists():
            return p
    # last resort: walk user's home (may be slow)
    home = Path.home()
    for dirpath, dirnames, filenames in os.walk(str(home)):
        if filename in filenames:
            return Path(dirpath) / filename
    return None


def ocr_file(p: Path):
    if not p or not p.exists():
        return ''
    suffix = p.suffix.lower()
    if suffix == '.pdf':
        return ''
    try:
        img = Image.open(p)
    except Exception:
        return ''

    # Only use EasyOCR here
    if not EASYOCR_OK:
        return ''
    try:
        reader = easyocr.Reader(['en'], gpu=False)
        results = reader.readtext(str(p))
        text = '\n'.join([r[1] for r in results if r and len(r) > 1])
        return text
    except Exception:
        return ''


def main():
    with app.app_context():
        receipts = Receipt.query.order_by(Receipt.id).all()
        updated = 0
        for r in receipts:
            use_text = ''
            raw = (r.raw_text or '').strip()
            if raw and not raw.startswith('['):
                use_text = raw
            else:
                # try to locate file and OCR it
                p = find_file(r.filename)
                if p:
                    use_text = ocr_file(p)
            if not use_text:
                # nothing to extract
                continue
            fields = extract_fields(use_text)
            changed = False
            # always update raw_text if different
            if r.raw_text != fields.get('raw_text'):
                r.raw_text = fields.get('raw_text')
                changed = True

            # date: prefer extracted, fallback to more patterns
            date_val = fields.get('date') or ''
            if not date_val:
                m = None
                m = re.search(r"Date[:\s]*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", use_text, flags=re.IGNORECASE)
                if m:
                    date_val = m.group(1)
                else:
                    lines = [ln.strip() for ln in use_text.splitlines()]
                    for i, line in enumerate(lines):
                        if not line:
                            continue
                        if re.search(r"\bdate\b", line, flags=re.IGNORECASE):
                            m2 = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", line)
                            if m2:
                                date_val = m2.group(1)
                                break
                            for j in range(i + 1, min(i + 4, len(lines))):
                                nxt = lines[j]
                                if not nxt:
                                    continue
                                m3 = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", nxt)
                                if m3:
                                    date_val = m3.group(1)
                                    break
                            if date_val:
                                break
            if (r.date or '') != (date_val or ''):
                r.date = date_val
                changed = True

            # total: prefer extracted, fallback to 'amount' or last decimal number
            total_val = fields.get('total') or _pick_total_from_text(use_text) or ''
            if total_val:
                total_val = _normalize_amount(total_val)
            if (r.total or '') != (total_val or ''):
                r.total = total_val
                changed = True
            if changed:
                updated += 1
        if updated:
            db.session.commit()
        print('Reprocessed', updated, 'receipts')


if __name__ == '__main__':
    main()
