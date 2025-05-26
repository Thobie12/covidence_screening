from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import os
import logging
import csv
from utils import *
from google import genai
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential
from google.genai import types
import time
import functools
import pandas as pd
import random
# from google.genai.types import Tool, GenerateContentConfig, GoogleSearch

load_dotenv()
logger = init_logging()


def log_exceptions(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Exception in {func.__name__}: {e}")
            raise
    return wrapper



# google_search_tool = Tool(
#     google_search=GoogleSearch()
# )


def load_existing_titles(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=["title", "abstract", "decision", "justification"]).set_index("title")

    return pd.read_csv(path).drop_duplicates(subset=["title"], keep='first').set_index("title")


@limits(calls=100, period=60)
@sleep_and_retry
def gemini_decision(title, abstract, source_info):
    api_keys = os.getenv("GEMINI_API_KEY", "").split(",")
    if not api_keys:
        logger.error("No GEMINI_API_KEY found in environment.")
        return None, 0

    full_prompt = USER_PROMPT + f"""
        Title: {title}
        Abstract: {abstract}
        Source Info: {source_info}
        """

    for key in random.sample(api_keys, len(api_keys)): 
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model="gemini-2.5-pro-preview-05-06",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ClassificationResult,
                    system_instruction=SYSTEM_PROMPT,
                ),
                contents=full_prompt,
            )

            token_usage = getattr(response.usage_metadata, 'total_token_count', 0)
            if token_usage:
                logger.info(f"Token Usage: {token_usage}")
            else:
                logger.warning("Token usage information not available.")

            if not hasattr(response, "parsed") or not response.parsed:
                logger.error(f"No parsed response from Gemini with key ending in {key[-4:]}.")
                continue  

            parsed: ClassificationResult = response.parsed
            logger.info(f"Gemini classification: {parsed.classification.value}")
            logger.info(f"Gemini justification: {parsed.justification}")

            return parsed, token_usage

        except Exception as e:
            logger.warning(f"API key ending in {key[-4:]} failed: {e}")

    logger.error("All API keys failed.")
    return None, 0
        
        

def handle_popover(page):
    try:
        if page.is_visible('button[aria-label="Close popover"]'):
            page.click('button[aria-label="Close popover"]')
    except Exception as e:
        logger.debug(f"Popover close attempt failed: {e}")


def extract_study_elements(top_study):
    try:
        title = top_study.query_selector('h3.title').inner_text().strip()
        abstract = top_study.query_selector(
            'div.abstract').inner_text().strip()
        source_info = top_study.query_selector(
            'div.source-info').inner_text().strip()
        return title, abstract, source_info
    except Exception as e:
        logger.warning(f"Failed to extract study details: {e}")
        return "", "", ""


def vote_on_study(top_study, value):
    try:
        top_study.query_selector(
            f'td.vote button[value="{value}"]').click(force=True)
        logger.info(f"Voted '{value}' on study.")
    except Exception as e:
        logger.warning(f"Could not vote '{value}' on study: {e}")


@log_exceptions
@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=10)
)
def classify_single_article(page, seen_titles):
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector('tr[id^="study-"]', timeout=6000)

    handle_popover(page)

    top_study = page.query_selector('tr[id^="study-"]')
    if not top_study:
        logger.info("No study found.")
        return None, 0

    title, abstract, source_info = extract_study_elements(top_study)

    if not title or not abstract:
        logger.warning("Missing title or abstract. Voting 'Maybe'.")
        vote_on_study(top_study, "Maybe")
        return {
            "title": title or "[MISSING]",
            "abstract": abstract or "[MISSING]",
            "decision": "Maybe",
            "justification": "Title or abstract not found."
        }, 0

    if title in seen_titles.index:
        existing = seen_titles.loc[title]
        logger.info(f"Already classified '{title}' as {existing['decision']}")
        return existing.to_dict(), 0

    logger.info(f"Processing: {title}")
    logger.debug(f"Abstract: {abstract}")
    logger.debug(f"Source Info: {source_info}")

    classification_result, tokens_used = gemini_decision(
        title, abstract, source_info)

    if not classification_result:
        logger.error("Gemini returned no classification. Skipping.")
        return None, 0

    decision_map = {
        "Include": "Yes",
        "Exclude": "No",
        "Maybe": "Maybe"
    }

    vote = decision_map.get(
        classification_result.classification.value, "Maybe")
    vote_on_study(top_study, vote)

    return {
        "title": title,
        "abstract": abstract,
        "decision": classification_result.classification.value,
        "justification": classification_result.justification
    }, tokens_used


def main():
    seen_titles = load_existing_titles(OUTPUT_CSV)
    processed_articles = len(seen_titles)
    total_tokens = 0
    processed_data = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        page.goto(SIGN_IN_URL)
        page.fill('#session_email', os.getenv("COVID_ID"))
        page.fill('#session_password', os.getenv("COVID_PASSWORD"))
        page.click('input[name="commit"]')
        page.wait_for_load_state("networkidle")

        page.goto(SCREENING_URL)

        try:
            while processed_articles < ARTICLES_TO_PROCESS:
                start_time = time.time()
                article, tokens_used = classify_single_article(
                    page, seen_titles)
                if article:
                    processed_data.append(article)
                    processed_articles += 1
                    total_tokens += tokens_used
                    logger.info(f"Processed articles: {processed_articles}")
                else:
                    logger.info("Not a valid study. Moving on...")
                    # break
                elapsed_time = time.time() - start_time
                logger.info(
                    f"Time taken for this article: {elapsed_time:.2f} seconds")
                logger.info("*" * 30 + '\n')
                # page.wait_for_timeout(500)

        except Exception as e:
            logger.exception("An error occurred during classification loop")

        finally:
            logger.info(f"Total processed articles: {processed_articles}")
            logger.info(f"Total tokens used: {total_tokens}")
            logger.info("Writing processed data to CSV...")

            output_csv_exists = os.path.exists(OUTPUT_CSV)
            with open(OUTPUT_CSV, 'a+', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=[
                                        "title", "abstract", "decision", "justification"])
                if not output_csv_exists:
                    writer.writeheader()
                writer.writerows(processed_data)

            logger.info("Data written to CSV successfully.")
            time.sleep(5)
            browser.close()


if __name__ == "__main__":
    main()
