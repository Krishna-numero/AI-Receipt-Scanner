# SENSE BILL - AI Receipt Scanner

A Flask-based web application that allows users to upload receipt images, automatically extract key information using OCR (Optical Character Recognition)s, store the data in SQLite, and export records as CSV files.

## Table of Contents
- [Features](#features)
- [Project Architecture](#project-architecture)
- [Installation & Setup](#installation--setup)
- [Running the Application](#running-the-application)
- [How It Works - Step by Step](#how-it-works---step-by-step)
- [Available Routes & Endpoints](#available-routes--endpoints)
- [Commands & CLI Usage](#commands--cli-usage)
- [Database Schema](#database-schema)
- [Supported File Formats](#supported-file-formats)
- [Important Notes](#important-notes)

---

## Features

✅ **User Authentication** - User registration and login with secure password hashing
✅ **Receipt Upload** - Upload receipt images (PNG, JPG, WEBP)
✅ **OCR Processing** - Extract text from receipts using EasyOCR
✅ **Field Extraction** - Automatically extract date, total amount, and bill category
✅ **Data Storage** - Store extracted data in SQLite database
✅ **CSV Export** - Export receipts as CSV with date range filtering
✅ **Receipt Management** - View, reprocess, and delete receipts
✅ **Batch Reprocessing** - Re-run OCR on all receipts in the background
✅ **Category Detection** - Automatically categorize bills (Grocery, Restaurant, Utilities, etc.)

---

## Project Architecture

```
AI-Receipt-Scanner/
├── app.py                    # Main Flask application with all routes
├── reprocess_receipts.py     # CLI utility for batch reprocessing
├── requirements.txt          # Python dependencies
├── receipts.db              # SQLite database (auto-created)
├── static/
│   └── styles.css           # UI styling
├── templates/               # HTML templates
│   ├── base.html            # Base template (header, navigation)
│   ├── index.html           # Dashboard (list all receipts)
│   ├── login.html           # Login page
│   ├── register.html        # Registration page
│   ├── upload.html          # Receipt upload form
│   └── receipt.html         # Single receipt details view
└── uploads/                 # Uploaded receipt images
```

---

## Installation & Setup

### Step 1: Create a Virtual Environment

Create an isolated Python environment to avoid dependency conflicts:

**Windows:**
```powershell
python -m venv venv
venv\Scripts\activate
```

**macOS/Linux:**
```bash
python -m venv venv
source venv/bin/activate
```

### Step 2: Install Dependencies

Install all required packages from requirements.txt:

```bash
pip install -r requirements.txt
```

**Dependencies:**
- `Flask >= 2.0` - Web framework
- `Flask-SQLAlchemy >= 3.0` - ORM for database
- `SQLAlchemy >= 1.4` - SQL toolkit
- `Pillow >= 9.0` - Image processing
- `Werkzeug >= 2.0` - WSGI utilities & password hashing
- `numpy >= 1.24` - Numerical computing (required by EasyOCR)
- `easyocr >= 1.7` - OCR engine

### Step 3: First Run (Database Initialization)

When you run the app for the first time, it will automatically create `receipts.db`:

```bash
python app.py
```

The database will be initialized with the required tables (`User`, `Receipt`).

---

## Running the Application

### Start the Flask Development Server

```bash
python app.py
```

**Output:**
```
WARNING in app.run_simple (11.0.0-dev): This is a development server. Do not use it in production applications.
 * Running on http://127.0.0.1:5000
```

Open your browser and navigate to:
```
http://127.0.0.1:5000
```

You'll see the login page. If you don't have an account, click "Register" to create one.

---

## How It Works - Step by Step

### 1. **User Registration**
   - Navigate to `/register`
   - Enter a username and password
   - Click "Register"
   - You'll be redirected to the login page

### 2. **User Login**
   - Navigate to `/login`
   - Enter your credentials
   - Session is created (stored in Flask session)
   - Redirected to the home dashboard

### 3. **Upload Receipt**
   - Click "Upload Receipt" button
   - Select an image file (PNG, JPG, WEBP)
   - **Optionally** enter a manual date (overrides OCR date detection)
   - Click "Upload & Process"

### 4. **OCR Processing** (Automatic)
   When you upload a receipt:
   - The image is saved to the `uploads/` folder
   - EasyOCR reads the image and extracts all text
   - The text is parsed to extract:
     - **Date** - Uses regex patterns to find dates in formats (YYYY-MM-DD, DD/MM/YYYY, etc.)
     - **Total Amount** - Finds currency symbols or amount keywords
     - **Category** - Matches text against a category dictionary
   - Data is stored in the SQLite database

### 5. **View Dashboard**
   - `/` (home page) shows all your receipts in a table
   - Each receipt shows: ID, Date, Total, Category, Filename, Created Time
   - Click a receipt row to view full details

### 6. **View Receipt Details**
   - Click on any receipt to see `/receipt/<id>`
   - Shows extracted text, all fields, filename
   - Options to reprocess or delete the receipt

### 7. **Export Data**
   - Use the date range filter on the dashboard
   - Click "Export as CSV"
   - Downloads CSV with all matching receipts + total sum

### 8. **Reprocess All Receipts**
   - Click "Reprocess All Receipts" button
   - Runs OCR again on all stored receipt images in the background
   - Useful if images weren't clear before

---

## Available Routes & Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Dashboard - list all receipts for logged-in user |
| `/register` | GET, POST | User registration page |
| `/login` | GET, POST | User login page |
| `/logout` | GET | Clear session and redirect to login |
| `/upload` | GET, POST | Upload and process a new receipt |
| `/receipt/<id>` | GET | View detailed information for receipt ID |
| `/uploads/<filename>` | GET | Serve uploaded receipt image |
| `/export` | GET | Export receipts as CSV (with optional date range) |
| `/export?start=YYYY-MM-DD&end=YYYY-MM-DD` | GET | Export CSV for specific date range |
| `/reprocess` | POST | Re-run OCR on all receipts (background task) |
| `/reprocess/<id>` | POST | Re-run OCR on specific receipt ID |
| `/delete/<id>` | POST | Delete a receipt and its uploaded image |

---

## Commands & CLI Usage

### Running the Application

**Start development server with debug mode:**
```bash
python app.py
```

### Environment-Based Configuration

**Set a custom secret key (for production):**
```bash
set SECRET_KEY=your-secret-key-here  # Windows
export SECRET_KEY=your-secret-key-here  # macOS/Linux
python app.py
```

**Set Flask environment:**
```bash
set FLASK_ENV=production  # Windows
export FLASK_ENV=production  # macOS/Linux
```

### Database Commands (Python REPL)

**Check all users:**
```python
python -c "from app import app, User, db; app.app_context().push(); print([u.username for u in User.query.all()])"
```

**Check receipt count:**
```python
python -c "from app import app, Receipt, db; app.app_context().push(); print(f'Total receipts: {Receipt.query.count()}')"
```

**Clear database (WARNING - deletes all data):**
```python
python -c "from app import app, db; app.app_context().push(); db.drop_all(); db.create_all(); print('Database reset')"
```

### Batch Reprocessing via CLI

**Reprocess all receipts (Python script):**
```bash
python reprocess_receipts.py
```

This script:
- Searches for receipt images in: `uploads/`, `Downloads/`, `Desktop/`, `Documents/`, `Pictures/`
- Re-runs OCR on each image
- Updates fields in the database
- Reports how many receipts were updated

---

## Database Schema

### User Table
```sql
CREATE TABLE user (
    id INTEGER PRIMARY KEY,
    username VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL
);
```

### Receipt Table
```sql
CREATE TABLE receipt (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    filename VARCHAR(300) NOT NULL,
    date VARCHAR(50),
    total VARCHAR(50),
    bill_category VARCHAR(100),
    raw_text TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Fields Explained:**
- `id` - Unique receipt ID
- `user_id` - Owner of the receipt
- `filename` - Uploaded image filename
- `date` - Extracted or manually entered date
- `total` - Extracted total amount
- `bill_category` - Auto-detected category (Grocery, Restaurant, etc.)
- `raw_text` - Full OCR extracted text
- `created_at` - Timestamp when receipt was uploaded

---

## Supported File Formats

✅ **Supported:** PNG, JPG, JPEG, WEBP

❌ **Not Supported:** PDF (no PDF-to-image conversion included)

**If you have PDFs:** Convert them to images (PNG/JPG) first using external tools, then upload.

---

## OCR & Field Extraction Logic

### Date Detection
Looks for patterns in these formats:
- `YYYY-MM-DD` (ISO format)
- `DD/MM/YYYY` (European)
- `MM/DD/YYYY` (US)
- Manual date override (user can enter on upload form)

### Total Amount Detection
1. Looks for currency symbols: `$`, `€`, `£`
2. Searches for keywords: `total`, `amount due`, `grand total`
3. Falls back to finding largest decimal number in text
4. Normalizes by removing thousands separators

### Category Detection
Matches keywords against 8 predefined categories:
- **Grocery** - walmart, costco, whole foods, supermarket
- **Restaurant** - pizza, cafe, burger, dining
- **Utilities** - electricity, water, gas, power bill
- **Transportation** - gas, fuel, parking, airline, hotel
- **Healthcare** - pharmacy, hospital, clinic, medical
- **Retail** - store, shopping, mall, clothing
- **Entertainment** - movie, cinema, concert, ticket
- **Education** - school, university, tuition, book
- **Other** (if no keywords match)

---

## Important Notes

⚠️ **Security Considerations:**
- This is a demo application. For production use:
  - Use secure session storage (not in-memory)
  - Enable HTTPS/SSL
  - Add CSRF protection
  - Implement rate limiting
  - Validate file uploads more strictly
  - Use environment variables for sensitive config

📁 **Database Location:**
- SQLite database stored at: `receipts.db` (in project root)
- Uploaded images stored in: `uploads/` folder

🔄 **OCR Performance:**
- First OCR run loads the EasyOCR model (~300-500MB)
- Subsequent calls reuse the loaded model (faster)
- GPU acceleration is disabled (set to CPU-only in code)

🖼️ **Image Processing:**
- High-resolution images processed with auto-contrast enhancement
- Fallback resizing for blurry images
- Supports image rotation and format conversion via Pillow

💾 **Data Retention:**
- Receipts are only deleted when user explicitly deletes them
- Export includes a total sum line at the bottom of CSV
- Date ranges are inclusive of start and end dates

---

## Troubleshooting

**Issue: "EasyOCR not installed"**
```bash
pip install easyocr>=1.7
```

**Issue: "No module named 'flask'"**
```bash
pip install -r requirements.txt
```

**Issue: Database locked error**
- Close other instances of the app
- Restart the Flask server

**Issue: OCR produces no text**
- Try uploading a clearer/higher-resolution image
- Use the reprocess button to retry with enhanced processing

---

## License & Notes

This is a minimal demonstration project showcasing receipt processing with Flask and EasyOCR. Customize and extend as needed for your use case.
