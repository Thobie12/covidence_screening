import logging
import colorlog
from pydantic import BaseModel, Field
from enum import Enum


class Classification(Enum):
    INCLUDE = "Include"
    EXCLUDE = "Exclude"
    MAYBE = "Maybe"


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


ARTICLES_TO_PROCESS = 2900
OUTPUT_CSV = 'processed_articles.csv'
SIGN_IN_URL = "https://app.covidence.org"
SCREENING_URL = "https://app.covidence.org/reviews/520996/review_studies/screen?filter=vote_required_from"

with open('protocol.txt', 'r') as file:
    protocol = file.read()

title = "Title: Retinal Hemorrhage Patterns in Abusive vs. Non-Abusive Head Trauma in Children"

SYSTEM_PROMPT = """
You are an AI research assistant specializing in systematic reviews. The user will provide a protocol for a review titled "Retinal Hemorrhage Patterns in Abusive vs. Non-Abusive Head Trauma in Children." and you are supposed to classify articles based on this protocol as to whether they should be included, excluded, or require further review.
Your primary directive is to master this specific protocol. All your assistance—including clarifying details, evaluating studies against inclusion/exclusion criteria, identifying data for extraction, and supporting manuscript development—must be strictly grounded in the provided protocol's content, particularly its objectives and criteria.
Maintain a precise, objective tone, and always base your reasoning on the given protocol. Strongly prefer a definitive classification like Include or Exclude. Use 'Maybe' only when the information is genuinely insufficient to make a clear decision.
"""

USER_PROMPT = f"""

This is the protocol that you have to strictly follow:
<>
{protocol}
<>

I will provide you with the title and abstract of a research article. Based solely on the systematic review protocol above ({title}), please perform the following:
Classify the article as one of the following:
Include: The article clearly meets all inclusion criteria and does not meet any exclusion criteria based on the information provided.
Exclude: The article clearly meets one or more exclusion criteria OR fails to meet one or more critical inclusion criteria based on the information provided.
Maybe (Requires Full-Text Review): The provided information is insufficient to make a definitive 'Include' or 'Exclude' decision, but the article does not appear to be immediately excludable.
Provide a brief justification for your classification.
For Include, briefly state how it meets key inclusion criteria.
For Exclude, clearly state which specific inclusion criterion it fails OR which specific exclusion criterion (by number, if possible) it meets.
For Maybe, explain what specific information is missing or ambiguous in the provided text that requires checking the full article against the protocol's criteria.

Strongly prefer a definitive classification like Include or Exclude. Use 'Maybe' only when the information is genuinely insufficient to make a clear decision. You can also search the web for more information about the article to help you make a decision.

Here is the title and abstract of the article:

"""


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

    file_handler = logging.FileHandler(f'covidence.log', mode='w')
    file_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    return logger
