import argparse
import json
import os
from pathlib import Path
from urllib import request, error

# Ensure OCR is enabled locally when importing app helpers
os.environ.setdefault("OCR_ENABLED", "1")

from app import ocr_path, extract_fields  # noqa: E402


def _post_json(url: str, token: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True}
    except error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            return {"ok": False, "status": e.code, "body": body}
        except Exception:
            return {"ok": False, "status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _iter_files(file_list, dir_path):
    if file_list:
        for f in file_list:
            yield Path(f)
    if dir_path:
        for p in Path(dir_path).iterdir():
            if p.is_file():
                yield p


def main():
    parser = argparse.ArgumentParser(description="Run OCR locally and sync results to Render.")
    parser.add_argument("--url", required=True, help="Base URL (e.g. https://sensebill.onrender.com)")
    parser.add_argument("--token", required=True, help="OCR sync token (OCR_SYNC_TOKEN)")
    parser.add_argument("--username", required=True, help="Username to associate receipts with")
    parser.add_argument("--file", action="append", help="Receipt file path (repeatable)")
    parser.add_argument("--dir", help="Directory of receipt images to process")
    parser.add_argument("--receipt-id", type=int, help="Optional receipt ID to update")
    args = parser.parse_args()

    api_url = args.url.rstrip("/") + "/api/ocr_sync"
    files = list(_iter_files(args.file, args.dir))
    if not files:
        raise SystemExit("No files provided. Use --file or --dir.")

    for p in files:
        if not p.exists():
            print(f"Skip (missing): {p}")
            continue
        text = ocr_path(p)
        fields = extract_fields(text)
        payload = {
            "username": args.username,
            "original_filename": p.name,
            "date": fields.get("date"),
            "total": fields.get("total"),
            "bill_category": fields.get("bill_category"),
            "raw_text": fields.get("raw_text"),
        }
        if args.receipt_id:
            payload["receipt_id"] = args.receipt_id

        res = _post_json(api_url, args.token, payload)
        if res.get("ok"):
            print(f"Synced: {p.name}")
        else:
            print(f"Failed: {p.name} -> {res}")


if __name__ == "__main__":
    main()
