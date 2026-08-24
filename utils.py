import os
import json
import logging
import colorlog
from pydantic import BaseModel, Field
from enum import Enum
from typing import TypedDict, List, Dict
from dotenv import load_dotenv

# Load .env here (not in main.py) so these values are available the moment
# this module is imported, regardless of import order.
load_dotenv()


class Classification(Enum):
    INCLUDE = "Include"
    EXCLUDE = "Exclude"
    MAYBE = "Maybe"


class Article(TypedDict):
    title: str
    abstract: str
    decision: str
    justification: str


decision_map = {
    "Include": "Yes",
    "Exclude": "No",
    "Maybe": "Maybe"
}


class ClassificationResult(BaseModel):
    classification: Classification = Field(
        ..., description="Classification of the article: Include, Exclude, or Maybe")
    justification: str = Field(...,
                               description="Justification for the classification")


# ---------------------------------------------------------------------------
# Everything below used to be hardcoded for one specific review. It now reads
# from environment variables (see .env.sample) so this repo can be reused for
# ANY Covidence review without editing code.
# ---------------------------------------------------------------------------

ARTICLES_TO_PROCESS = int(os.getenv("ARTICLES_TO_PROCESS", "3700"))
OUTPUT_CSV = os.getenv("OUTPUT_CSV", "processed_articles.csv")
SIGN_IN_URL = "https://app.covidence.org"

# Set COVIDENCE_REVIEW_URL in .env to your own review's "vote required from"
# screening queue URL (copy it straight from your browser address bar while
# on your Covidence screening queue page).
SCREENING_URL = os.getenv("COVIDENCE_REVIEW_URL", "")
if not SCREENING_URL:
    raise RuntimeError(
        "COVIDENCE_REVIEW_URL is not set in .env. Open your Covidence "
        "review's screening queue in a browser and paste that URL into .env."
    )

# Set REVIEW_TITLE in .env to a short human-readable title for your review.
REVIEW_TITLE = os.getenv("REVIEW_TITLE", "your systematic review")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

# Fraction (0.0-1.0) of AI decisions that get randomly held for a human
# spot-check before the vote is submitted. Override with --spot-check-rate.
SPOT_CHECK_RATE = float(os.getenv("SPOT_CHECK_RATE", "0.1"))

# Where confirmed human corrections (from spot-check overrides) are saved so
# future runs — and later articles in THIS run — benefit from them.
LEARNED_CORRECTIONS_FILE = os.getenv(
    "LEARNED_CORRECTIONS_FILE", "learned_corrections.jsonl")

# How many of the most recent corrections get fed back into the prompt.
# Kept bounded so the prompt doesn't grow without limit over a long review.
MAX_LEARNED_CORRECTIONS = int(os.getenv("MAX_LEARNED_CORRECTIONS", "25"))

_protocol_path = os.getenv("PROTOCOL_FILE", "protocol.txt")
if not os.path.exists(_protocol_path):
    raise RuntimeError(
        f"Protocol file '{_protocol_path}' not found. Create it with your "
        "systematic review's inclusion/exclusion criteria before running."
    )
with open(_protocol_path, 'r') as file:
    protocol = file.read()

SYSTEM_PROMPT = f"""
You are an AI research assistant specializing in systematic reviews. The user will provide a protocol for a review titled "{REVIEW_TITLE}" and you are supposed to classify articles based on this protocol as to whether they should be included, excluded, or require further review.
Your primary directive is to master this specific protocol. All your assistance—including clarifying details, evaluating studies against inclusion/exclusion criteria, identifying data for extraction, and supporting manuscript development—must be strictly grounded in the provided protocol's content, particularly its objectives and criteria.
Maintain a precise, objective tone, and always base your reasoning on the given protocol. Strongly prefer a definitive classification like Include or Exclude. Use 'Maybe' only when the information is genuinely insufficient to make a clear decision.

You MUST respond with ONLY a JSON object matching this exact schema, and nothing else (no markdown fences, no commentary):
{{"classification": "Include" | "Exclude" | "Maybe", "justification": "<brief justification>"}}
"""

USER_PROMPT = f"""
This is the protocol that you have to strictly follow:
<>
{protocol}
<>

I will provide you with the title and abstract of a research article. Based solely on the systematic review protocol above ({REVIEW_TITLE}), please perform the following:
Classify the article as one of the following:
Include: The article clearly meets all inclusion criteria and does not meet any exclusion criteria based on the information provided.
Exclude: The article clearly meets one or more exclusion criteria OR fails to meet one or more critical inclusion criteria based on the information provided.
Maybe (Requires Full-Text Review): The provided information is insufficient to make a definitive 'Include' or 'Exclude' decision, but the article does not appear to be immediately excludable.
Provide a brief justification for your classification.
For Include, briefly state how it meets key inclusion criteria.
For Exclude, clearly state which specific inclusion criterion it fails OR which specific exclusion criterion (by number, if possible) it meets.
For Maybe, explain what specific information is missing or ambiguous in the provided text that requires checking the full article against the protocol's criteria.

Strongly prefer a definitive classification like Include or Exclude. Use 'Maybe' only when the information is genuinely insufficient to make a clear decision.

Here is the title and abstract of the article:

"""


def load_learned_corrections() -> List[Dict]:
    """Load every correction saved so far (across all past runs)."""
    if not os.path.exists(LEARNED_CORRECTIONS_FILE):
        return []
    corrections = []
    with open(LEARNED_CORRECTIONS_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                corrections.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return corrections


def append_learned_correction(title: str, ai_decision: str, human_decision: str, reason: str) -> None:
    """Persist one human correction immediately (append-only) so it survives
    even if the script crashes right after, and is available to the very
    next classification call."""
    entry = {
        "title": title,
        "ai_decision": ai_decision,
        "human_decision": human_decision,
        "reason": reason,
    }
    with open(LEARNED_CORRECTIONS_FILE, 'a') as f:
        f.write(json.dumps(entry) + "\n")


def format_learned_corrections(corrections: List[Dict]) -> str:
    """Render the most recent corrections as a short lessons-learned block
    to inject into the classification prompt. Only corrections with a
    human-provided reason are useful here — a bare 'X became Y' with no
    explanation doesn't generalize to other articles."""
    with_reasons = [c for c in corrections if c.get("reason", "").strip()]
    if not with_reasons:
        return ""

    recent = with_reasons[-MAX_LEARNED_CORRECTIONS:]
    lines = [
        f'- Previously misclassified as "{c["ai_decision"]}" '
        f'(should have been "{c["human_decision"]}"): {c["reason"].strip()}'
        for c in recent
    ]
    return (
        "\nLESSONS FROM PAST HUMAN CORRECTIONS on this same review — a human "
        "reviewer caught these mistakes before; apply the same reasoning to "
        "avoid repeating them on similar articles:\n"
        + "\n".join(lines) + "\n"
    )


def init_logging():
    logger = logging.getLogger('covidence')
    logger.setLevel(logging.DEBUG)

    color_formatter = colorlog.ColoredFormatter(
        "%(log_color)s - %(levelname)s - %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
        }
    )
    # console logs. Comment this if you do not want console output
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(color_formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(f'covidence.log', mode='a')
    file_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    return logger
