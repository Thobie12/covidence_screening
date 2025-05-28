from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from argparse import ArgumentParser
import os
import time
import pandas as pd
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential
from google import genai
from google.genai import types
from utils import *

load_dotenv()
logger = init_logging()


class CovidenceClassifier:
    def __init__(self, output_csv: str, headless: bool, article_limit: int):
        self.output_csv = output_csv
        self.headless = headless
        self.article_limit = article_limit
        self.total_tokens = 0
        self.max_tries = 0
        self.seen_titles = self.load_existing_titles(self.output_csv)
        self.processed_articles = self.seen_titles.shape[0]
        self.new_articles = []
        self.gemini_clients = {key: genai.Client(api_key=key.strip()) for key in os.getenv(
            "GEMINI_API_KEY", "").split(",") if key}

    @staticmethod
    def load_existing_titles(csv_path):
        if not os.path.exists(csv_path):
            return pd.DataFrame(columns=["title", "abstract", "decision", "justification"]).set_index("title")
        return pd.read_csv(csv_path, index_col="title")

    @staticmethod
    def handle_popover(page):
        try:
            if page.is_visible('button[aria-label="Close popover"]'):
                page.click('button[aria-label="Close popover"]')
        except Exception as e:
            logger.debug(f"Popover close attempt failed: {e}")

    @staticmethod
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

    @staticmethod
    def vote_on_study(top_study, value):
        try:
            top_study.query_selector(
                f'td.vote button[value="{value}"]').click(force=True)
            logger.info(f"Voted '{value}' on study.")
        except Exception as e:
            logger.warning(f"Could not vote '{value}' on study: {e}")

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def set_up_browser(self, p):
        browser = p.chromium.launch(headless=self.headless)
        page = browser.new_page()

        page.goto(SIGN_IN_URL, timeout=60000)
        page.fill('#session_email', os.getenv("COVID_ID"))
        page.fill('#session_password', os.getenv("COVID_PASSWORD"))
        page.click('input[name="commit"]')
        page.wait_for_load_state("networkidle", timeout=60000)

        page.goto(SCREENING_URL, timeout=60000)
        return browser, page

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=10))
    def classify_single_article(self, page):
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_selector('tr[id^="study-"]', timeout=6000)

        self.handle_popover(page)

        top_study = page.query_selector('tr[id^="study-"]')
        if not top_study:
            logger.info("No study found.")
            return None, 0

        title, abstract, source_info = self.extract_study_elements(
            top_study)

        if not title or not abstract:
            logger.warning("Missing title or abstract. Voting 'Maybe'.")
            self.vote_on_study(top_study, "Maybe")
            return {
                "title": title or "[MISSING]",
                "abstract": abstract or "[MISSING]",
                "decision": "Maybe",
                "justification": "Title or abstract not found."
            }, 0

        logger.info(f"Processing: {title}")

        if title in self.seen_titles.index:
            rows = self.seen_titles.loc[title]
            existing = rows if isinstance(rows, pd.Series) else rows.iloc[0]
            logger.info(
                f"Already classified '{title}' as {existing['decision']}")
            vote = decision_map.get(existing["decision"], "Maybe")
            self.vote_on_study(top_study, vote)
            return existing.to_dict(), 0

        classification_result, tokens_used = self.gemini_decision(
            title, abstract, source_info)

        if not classification_result:
            logger.error("Gemini returned no classification. Skipping.")
            return None, 0

        vote = decision_map.get(
            classification_result.classification.value, "Maybe")
        self.vote_on_study(top_study, vote)

        return {
            "title": title,
            "abstract": abstract,
            "decision": classification_result.classification.value,
            "justification": classification_result.justification
        }, tokens_used

    @limits(calls=100, period=60)
    @sleep_and_retry
    def gemini_decision(self, title, abstract, source_info):
        api_keys = os.getenv("GEMINI_API_KEY", "").split(",")
        if not api_keys:
            logger.error("No GEMINI_API_KEY found in environment.")
            return None, 0

        full_prompt = USER_PROMPT + f"""
        Title: {title}
        Abstract: {abstract}
        Source Info: {source_info}
        """

        for key, client in self.gemini_clients.items():
            try:
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ClassificationResult,
                        system_instruction=SYSTEM_PROMPT,
                    ),
                    contents=full_prompt,
                )

                token_usage = getattr(
                    response.usage_metadata, 'total_token_count', 0)
                if token_usage:
                    logger.info(f"Token Usage: {token_usage}")

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

    def save_article(self, article, tokens_used):
        self.new_articles.append(article)
        df = pd.DataFrame.from_records([article], index="title")
        self.seen_titles = pd.concat([self.seen_titles, df])
        self.total_tokens += tokens_used
        self.processed_articles += 1
        self.max_tries = 0

    def save_to_csv(self):
        if not self.new_articles:
            logger.info("No new articles to save.")
            return
        df = pd.DataFrame.from_records(self.new_articles, index="title")
        df.to_csv(self.output_csv, mode='a',
                  header=not os.path.exists(self.output_csv), index=True)
        logger.debug("Data saved.")

    def run(self):
        logger.debug("Starting Covidence classification script...")
        logger.info(
            f"Loaded {self.processed_articles} previously processed articles.")

        with sync_playwright() as p:
            browser, page = self.set_up_browser(p)

            try:
                while self.processed_articles < self.article_limit:
                    start = time.time()
                    article, tokens_used = self.classify_single_article(page)

                    if article:
                        self.save_article(article, tokens_used)
                        logger.info(
                            f"Processed articles: {self.processed_articles}")
                    else:
                        self.max_tries += 1
                        if self.max_tries >= 5:
                            logger.error("Max tries reached. Exiting.")
                            break
                        logger.info("Retrying as article was None.")

                    logger.info(
                        f"Time taken: {time.time() - start:.2f} seconds")
                    logger.debug("*" * 30 + "\n")

            except Exception as e:
                logger.exception(
                    "Unexpected error during classification loop.")

            finally:
                logger.info(f"Total processed: {self.processed_articles}")
                logger.info(f"Total tokens used: {self.total_tokens}")
                self.save_to_csv()
                logger.info("Closing browser...")
                browser.close()


if __name__ == "__main__":
    parser = ArgumentParser(
        description="Covidence Article Classification Script")
    parser.add_argument("--articles", type=int, default=ARTICLES_TO_PROCESS,
                        help="Number of articles to process")
    parser.add_argument("--output", type=str,
                        default=OUTPUT_CSV, help="Output CSV file")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode")
    args = parser.parse_args()

    classifier = CovidenceClassifier(
        output_csv=args.output,
        headless=args.headless,
        article_limit=args.articles
    )
    classifier.run()
