# Covidence Screener AI Automation

**Automate Covidence systematic review screening with Playwright + Google Gemini AI**

---

## 🔍 Overview

This project **automates title/abstract screening on [Covidence](https://www.covidence.org/)** by integrating:

* [Playwright (Python)](https://playwright.dev/python/) for browser automation (not web scraping, but *actual* browser interaction)
* [Google Gemini](https://ai.google.dev) (via `gemini-2.5-pro-preview` or later) for LLM-based article classification
* Your own systematic review protocol (in `protocol.txt`)
* Pydantic and Tenacity for validation, structured error handling, and retries
* CSV tracking to avoid duplicate processing

**Process:**

1. Script logs into Covidence with credentials from `.env`
2. Navigates to screening queue
3. Extracts title, abstract, and source info for each study
4. Sends these to Gemini LLM, enforcing your custom protocol
5. Automates the Yes/No/Maybe vote, logs the justification
6. Results are logged (console + `covidence.log`) and appended to `processed_articles.csv`

---

## 🧰 Dependencies

* `Python 3.10+`
* `playwright`
* `pydantic`
* `tenacity`
* `ratelimit`
* `google-generativeai`
* `dotenv`
* `pandas`
* `colorlog`

> All install via `uv run main.py` (using your `pyproject.toml`)

---

## 🗂️ Directory Structure

```
.
├── main.py                 # Main automation script
├── utils.py                # Logging, data classes, config, prompts
├── protocol.txt            # Your review protocol
├── processed_articles.csv  # Output: processed article results
├── .env                    # Credentials & Gemini API keys
├── covidence.log           # Logging output
├── .venv/                  # Python virtual environment
├── pyproject.toml          # Dependencies & metadata
└── README.md               # (You are here)
```

---

## ⚙️ Setup

**1. Clone the repo**

```bash
git clone https://github.com/yourname/covidence_screening.git
cd covidence_screening
```

**2. Prepare your `.env` file**

```env
COVID_ID=your_covidence_email
COVID_PASSWORD=your_password
GEMINI_API_KEY=key1,key2,key3
```

* *Multiple Gemini API keys are supported and **used in round-robin fashion** to avoid rate-limits.*

**3. Put your review protocol in `protocol.txt`**

* This is the set of rules the LLM will follow for every screening call.

---

## 🚀 Usage

**Command:**

```bash
uv run main.py [--articles N] [--output FILE] [--headless]
```

* `--articles` — Number of articles to process (default: 3700, see `ARTICLES_TO_PROCESS` in `utils.py`)
* `--output` — CSV output file (default: `processed_articles.csv`)
* `--headless` — Run browser in headless mode (default: False, i.e., browser window is shown for debug)

### **What actually happens:**

* Loads Covidence with supplied credentials
* Navigates to the screening queue (hardcoded `SCREENING_URL`)
* For each study row:

  * Extracts title, abstract, and source-info (via Playwright selectors)
  * **Checks for duplicates:** if title already in output CSV, votes accordingly and skips LLM call
  * Otherwise, sends to Gemini model (rotates through API keys on failure/rate-limit)
  * Maps LLM output (`Include`, `Exclude`, `Maybe`) to Covidence vote (`Yes`, `No`, `Maybe`)
  * Saves title, abstract, decision, justification to CSV
  * Detailed log to `covidence.log` and console

### **Important:**

* **If extraction fails** (missing title/abstract): votes `Maybe` and logs reason.
* **Retries**: On browser or API failure, up to 3 attempts (with exponential backoff).
* **Gemini usage:** Only “Include”, “Exclude”, or “Maybe” are valid, enforced by model prompt and validation.
* **No duplicate votes:** already-screened titles (by exact string match) are skipped.

---

## 🧪 Debugging

* Console and file logging (color-coded for console, plain for file).
* Debug specific issues in `covidence.log`
* **Visual mode** by default: browser window opens so you can see automation step-by-step.
  Use `--headless` for true automation.
* **Timeouts and retries**: all major actions (login, navigation, classification) are retried automatically.

---

## 🛡️ FAQ

* **Q: What if Gemini can’t classify?**
  **A:** Returns `Maybe`, with a justification.

* **Q: Does it process already-screened studies?**
  **A:** No — checks the output CSV by title.

* **Q: Can I use other models/providers?**
  **A:** Yes, if you update the `gemini_decision()` logic.

* **Q: Is my protocol enforced?**
  **A:** Yes — every call passes your entire protocol from `protocol.txt` and *the system prompt hard-codes the review topic*.

* **Q: Is this for commercial use?**
  **A:** **NO**. Academic/research only.

---

## ⚡ Technical Notes

* **Rate limits:**
  100 calls/min enforced via `ratelimit` decorator.
  Multiple API keys are *actively rotated* (not just for backup).

* **Output format:**
  All results saved as rows in `processed_articles.csv` with columns:
  `title`, `abstract`, `decision`, `justification`
  (title is the index).

* **Pydantic Models:**
  LLM is forced to return a valid enum (`Include`, `Exclude`, `Maybe`) and a free-text justification, or it fails validation and retries.

---

## 📜 License

Creative Commons Attribution-NonCommercial 4.0 International Public
LicensePlease see the [LICENSE](LICENSE) file for more details.
