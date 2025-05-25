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

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def load_existing_titles(path):
    if not os.path.exists(path):
        return set()
    with open(path, newline='', encoding='utf-8') as f:
        return {row["title"].strip() for row in csv.DictReader(f)}


@limits(calls=100, period=60)
@sleep_and_retry
def gemini_decision(title, abstract):
    full_prompt = USER_PROMPT + f"""\nTitle: {title}\nAbstract: {abstract}"""

    response = client.models.generate_content(
        model="gemini-2.5-pro-preview-05-06",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ClassificationResult,
            system_instruction=SYSTEM_PROMPT,
        ),
        contents=full_prompt,
    )

    if response.usage_metadata and response.usage_metadata.total_token_count:
        tot = response.usage_metadata.total_token_count
        logger.info(f"Token Usage: {tot}")
    else:
        logger.warning("Token usage information not available.")
        tot = 0
        
    if not response.parsed:
        logger.error(f"No parsed response received from Gemini. \n{response}")
        return None, 0

    parsed: ClassificationResult = response.parsed

    logger.info(f"Gemini classification: {parsed.classification.value}")
    logger.info(f"Gemini justification: {parsed.justification}")

    return parsed, tot


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
    
    if page.is_visible('button[aria-label="Close popover"]'):
        page.click('button[aria-label="Close popover"]')
    
    top_study = page.query_selector('tr[id^="study-"]')
    if not top_study:
        logger.info("No study found.")
        return None, 0  

    title = top_study.query_selector('h3.title').inner_text().strip()
    abstract = top_study.query_selector('div.abstract').inner_text().strip()
    
    if title in seen_titles:
        logger.warning(f"Study '{title}' has already been processed, skipping.")
        return None, 0

    if not title or not abstract:
        logger.warning("Title or abstract is empty, skipping.")
        return None, 0

    logger.info(f"Processing study: {title}")
    logger.info(f"Abstract: {abstract}")

    classification_result, tokens_used = gemini_decision(title, abstract)
    
    if not classification_result:
        logger.error("Classification result is None, skipping this article.")
        return None, 0

    value = "Yes" if classification_result.classification.value == "Include" else "No" if classification_result.classification.value == "Exclude" else "Maybe"
    top_study.query_selector(f'td.vote button[value="{value}"]').click(force=True)

    article_data = {
        "title": title,
        "abstract": abstract,
        "decision": classification_result.classification.value,
        "justification": classification_result.justification
    }

    return article_data, tokens_used

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
                article, tokens_used = classify_single_article(page, seen_titles)
                if article:
                    processed_data.append(article)
                    processed_articles += 1
                    total_tokens += tokens_used
                    logger.info(f"Processed articles: {processed_articles}")
                else:
                    logger.info("Not a valid study. Moving on...")
                    # break
                elapsed_time = time.time() - start_time
                logger.info(f"Time taken for this article: {elapsed_time:.2f} seconds")
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
                writer = csv.DictWriter(csvfile, fieldnames=["title", "abstract", "decision", "justification"])
                if not output_csv_exists:
                    writer.writeheader()
                writer.writerows(processed_data)

            logger.info("Data written to CSV successfully.")
            time.sleep(5)
            browser.close()

if __name__ == "__main__":
    main()
