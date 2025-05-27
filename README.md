````markdown
# Covidence Screener AI Automation

This project automates the title/abstract screening process on [Covidence](https://www.covidence.org/) using:

- [Playwright](https://playwright.dev/python/) for browser automation
- [Google Gemini](https://ai.google.dev) for intelligent article classification (via the `gemini-2.5-pro-preview` model)
- A custom systematic review protocol
- Pydantic and Tenacity for structured handling and retries
- CSV tracking to persist previous classifications

---

## 🔍 Overview

The script logs into Covidence, loads new studies for screening, and classifies them according to a user-supplied review protocol using a Gemini LLM.

It automates voting as:
- **Yes** for "Include"
- **No** for "Exclude"
- **Maybe** for undecidable abstracts

All classifications and justifications are logged and saved to a CSV.

---

## 🧠 Features

- Fully automated login and navigation to screening page
- Intelligent classification based on protocol
- Persistent tracking via `processed_articles.csv`
- Built-in retry and rate-limiting to avoid overloading the LLM API
- Uses environment variables and local `.env` config for credentials and API keys
- Detailed logging to `covidence.log`

---

## 🧰 Dependencies

This project uses Python 3.10 and the following key libraries:

- `playwright`
- `pydantic`
- `tenacity`
- `ratelimit`
- `google-generativeai`
- `dotenv`
- `pandas`
- `colorlog`

These will install automatically when you run `uv run`

---

## 🗂️ Directory Structure

```
.
├── main.py                 # Entry point script for automation
├── utils.py                # Logging and classification utilities
├── protocol.txt            # The review protocol for LLM
├── processed_articles.csv  # Output file to track decisions
├── .env                    # Stores credentials and API keys
├── covidence.log           # Log file for debug and audit
├── .venv/                  # Virtual environment (not tracked in Git)
├── pyproject.toml          # Project metadata and dependencies
└── README.md               # You're reading it!
```

---

## ⚙️ Setup

### 1. Clone the Repo

```bash
git clone https://github.com/yourname/covidence.git
cd covidence
```

### 2. Create `.env` File

Copy the following template into a `.env` file in the root directory:

```env
COVID_ID=your_covidence_email
COVID_PASSWORD=your_password
GEMINI_API_KEY=your_gemini_api_key1,your_gemini_api_key2
```

### 3. Add Your Review Protocol

Make sure your `protocol.txt` is present and contains the detailed inclusion/exclusion criteria.

---

## 🚀 Usage

Run the automation using:

```bash
uv run main.py
```

The script will:

1. Log into Covidence
2. Open the screening page
3. Extract each study's title and abstract
4. Classify using Gemini
5. Vote accordingly and log decision
6. Save results to `processed_articles.csv`

You can adjust how many articles to process by editing the `ARTICLES_TO_PROCESS` constant in `utils.py`.

---

## 🧪 Testing & Debugging

* To debug a specific issue, review `covidence.log` for detailed traces.
* Use `headless=False` (default) to visually inspect the Playwright browser.
* Adjust timeouts in `main.py` if you're experiencing network delays or slow loading.

---

## 🧼 Cleaning & Maintenance

* **CSV deduplication** is handled at load time via `drop_duplicates`.
* **Retry logic** is built-in to the classification method with exponential backoff.
* **Failed classifications** are logged and skipped.

---

## 🛡️ Notes

* Google Gemini API usage is rate-limited — avoid hitting quotas or exhausting keys.

---

## 🙋 FAQ

**Q: What happens if the model can’t classify?**
A: It returns `"Maybe"` and logs a justification.

**Q: Does it re-process previously classified titles?**
A: No — `processed_articles.csv` is checked on each run to avoid duplication.

**Q: Can I use a different model/provider?**
A: Yes, with slight modification to `gemini_decision()` logic.

---

## 📜 License

This repository is provided for academic and research use. Not for commercial deployment.
