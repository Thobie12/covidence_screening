from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import os
from argparse import ArgumentParser
import time
import pandas as pd
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential
from google import genai
from google.genai import types
from utils import *

load_dotenv()
logger = init_logging()


def log_exceptions(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Exception in {func.__name__}: {e}")
            raise
    return wrapper


def load_existing_titles(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=["title", "abstract", "decision", "justification"]).set_index("title")
    return pd.read_csv(path, index_col="title")


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

    for key in api_keys:
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

            token_usage = getattr(response.usage_metadata,
                                  'total_token_count', 0)
            if token_usage:
                logger.info(f"Token Usage: {token_usage}")
            else:
                logger.warning("Token usage information not available.")

            if not getattr(response, "parsed", None):
                logger.error(
                    f"No parsed response from Gemini with key ending in {key[-4:]}.")
                continue

            parsed: ClassificationResult = response.parsed
            logger.info(
                f"Gemini classification: {parsed.classification.value}")
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
    top_study.query_selector(
        f'td.vote button[value="{value}"]').click(force=True)
    logger.info(f"Voted '{value}' on study.")


@log_exceptions
@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=10))
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

    logger.info(f"Processing: {title}")

    if title in seen_titles.index:
        rows = seen_titles.loc[title]
        existing = rows if isinstance(rows, pd.Series) else rows.iloc[0]

        logger.info(f"Already classified '{title}' as {existing['decision']}")
        vote = decision_map.get(existing["decision"], "Maybe")
        vote_on_study(top_study, vote)
        return existing.to_dict(), 0

    classification_result, tokens_used = gemini_decision(
        title, abstract, source_info)

    if not classification_result:
        logger.error("Gemini returned no classification. Skipping.")
        return None, 0

    vote = decision_map.get(
        classification_result.classification.value, "Maybe")
    vote_on_study(top_study, vote)

    return {
        "title": title,
        "abstract": abstract,
        "decision": classification_result.classification.value,
        "justification": classification_result.justification
    }, tokens_used


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=10))
def set_up_browser(p, headless):
    browser = p.chromium.launch(headless=headless)
    page = browser.new_page()

    page.goto(SIGN_IN_URL, timeout=60000)
    page.fill('#session_email', os.getenv("COVID_ID"))
    page.fill('#session_password', os.getenv("COVID_PASSWORD"))
    page.click('input[name="commit"]')
    page.wait_for_load_state("networkidle", timeout=60000)

    page.goto(SCREENING_URL, timeout=60000)

    return browser, page


def main(articles, output_csv, headless):

    logger.debug("Starting Covidence classification script...")

    seen_titles = load_existing_titles(output_csv)
    processed_articles = seen_titles.shape[0]
    total_tokens, max_tries = 0, 0
    new_titles = pd.DataFrame(
        columns=["title", "abstract", "decision", "justification"]).set_index("title")

    logger.info(f"Loaded {processed_articles} previously processed articles.")

    with sync_playwright() as p:
        browser, page = set_up_browser(p, headless)

        try:
            while processed_articles < articles:
                start_time = time.time()
                article, tokens_used = classify_single_article(
                    page, seen_titles)

                if article:
                    new_titles = pd.concat(
                        [new_titles, pd.DataFrame.from_records([article], index="title")])
                    seen_titles = pd.concat(
                        [seen_titles, pd.DataFrame.from_records([article], index="title")])
                    processed_articles += 1
                    total_tokens += tokens_used
                    max_tries = 0
                    logger.info(f"Processed articles: {processed_articles}")
                else:
                    max_tries += 1
                    if max_tries >= 5:
                        logger.error("Max tries reached. Exiting.")
                        break
                    logger.info("Retrying as article was None.")

                logger.info(
                    f"Time taken: {time.time() - start_time:.2f} seconds")
                logger.debug("*" * 30 + "\n")

        except Exception as e:
            logger.exception("Error in classification loop")

        finally:
            logger.info(f"Total processed: {processed_articles}")
            logger.info(f"Total tokens used: {total_tokens}")

            new_titles.to_csv(output_csv, mode='a',
                              header=not os.path.exists(output_csv), index=True)

            logger.debug("Data saved.")
            time.sleep(5)
            browser.close()


if __name__ == "__main__":
    parser = ArgumentParser(
        description="Covidence Article Classification Script")
    parser.add_argument("--articles", type=int, default=ARTICLES_TO_PROCESS,
                        help="Number of articles to process")
    parser.add_argument("--output", type=str, default=OUTPUT_CSV,
                        help="Output CSV file for processed articles")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode")
    args = parser.parse_args()

    main(articles=args.articles, output_csv=args.output, headless=args.headless)
