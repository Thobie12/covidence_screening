import logging
import colorlog
from pydantic import BaseModel, Field
from typing import List, Dict
from enum import Enum

class Classification(Enum):
    INCLUDE = "Include"
    EXCLUDE = "Exclude"
    MAYBE = "Maybe"


class ClassificationResult(BaseModel):
    classification: Classification = Field(..., description="Classification of the article: Include, Exclude, or Maybe")
    justification: str = Field(..., description="Justification for the classification")


ARTICLES_TO_PROCESS = 6
OUTPUT_CSV = 'processed_articles.csv'
SIGN_IN_URL = "https://app.covidence.org"
SCREENING_URL = "https://app.covidence.org/reviews/520996/review_studies/screen?filter=vote_required_from"


SYSTEM_PROMPT= """
You are an AI research assistant specializing in systematic reviews. The user will provide a protocol for a review titled "Retinal Hemorrhage Patterns in Abusive vs. Non-Abusive Head Trauma in Children."
Your primary directive is to master this specific protocol. All your assistance—including clarifying details, evaluating studies against inclusion/exclusion criteria, identifying data for extraction, and supporting manuscript development—must be strictly grounded in the provided protocol's content, particularly its objectives and criteria.
Maintain a precise, objective tone, and always base your reasoning on the given protocol.
"""

USER_PROMPT = """

This is the protocol that you have to strictly follow:
<>
Title:
Retinal Hemorrhage Patterns in Abusive vs. Non-Abusive Head Trauma in Children: A Systematic Review

Background & Rationale:
Retinal hemorrhages (RH) are commonly associated with Abusive Head Trauma (AHT) in children. While RH can also occur in Non-Abusive Head Trauma (NAHT) and other conditions (e.g., infections, coagulopathies), distinguishing between abusive and non-abusive causes is critical due to the clinical and legal implications. Specific RH patterns (type, location, number) may aid in differential diagnosis.

Objectives:

Primary: Compare characteristics of RH in AHT vs. NAHT among children <5 years old.

Secondary:

Assess whether RH patterns can reliably distinguish AHT from NAHT.

Evaluate if combining RH with other clinical signs (e.g., intracranial hemorrhage) improves diagnostic accuracy.


Inclusion Criteria: 
1.	Articles published between 1999 to 2024. 
2.	Randomized and non-randomized controlled trials, observational studies (retrospective and prospective cohorts, case-control, case series), cross-sectional analyses and mixed-methods studies.
3.	Published articles. 
4.	Articles published in English.
5.	Articles with a population of infants (newborns to 5 years old)
6.	In non-abusive HT we will include accidental trauma, birth-related trauma, and selected medical conditions (e.g., coagulopathies, increased intracranial pressure), as these causes are frequently reported in pediatric populations and present diagnostic challenges.
7.	Abusive or non-abusive head trauma that have reported retinal hemorrhage
Exclusion Criteria: 
1.	Articles published outside of the specified timeline. 
2.	All sources of grey literature such as presentations from academic conferences, and studies and evaluations produced from Non-Governmental Organisations (NGOs), letter to the editors, short communications, reports and policy documents published by government agencies at the local or national level
3.	Articles in preprint
4.	Articles written in languages other than English or with no available translation.
5.	Articles focused on populations older than 5 years old.
6.	Exclude non-abusive causes that are extremely rare.
7.	Studies without relevant data

<>

I will provide you with the title and abstract of a research article. Based solely on the systematic review protocol above (Title: Retinal Hemorrhage Patterns in Abusive vs. Non-Abusive Head Trauma in Children), please perform the following:
Classify the article as one of the following:
Include: The article clearly meets all inclusion criteria and does not meet any exclusion criteria based on the information provided.
Exclude: The article clearly meets one or more exclusion criteria OR fails to meet one or more critical inclusion criteria based on the information provided.
Maybe (Requires Full-Text Review): The provided information is insufficient to make a definitive 'Include' or 'Exclude' decision, but the article does not appear to be immediately excludable.
Provide a brief justification for your classification.
For Include, briefly state how it meets key inclusion criteria.
For Exclude, clearly state which specific inclusion criterion it fails OR which specific exclusion criterion (by number, if possible) it meets.
For Maybe, explain what specific information is missing or ambiguous in the provided text that requires checking the full article against the protocol's criteria.
While a definitive 'Include' or 'Exclude' is preferred, prioritize accuracy. Do not force a classification if the provided information is genuinely insufficient.

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
    # console logs. Uncomment this if you do not want console output
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(color_formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(f'covidence.log', mode='w')
    file_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    return logger