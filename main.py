from typing import Optional, Tuple, Dict, Any, List
from playwright.sync_api import sync_playwright, Browser, Page, Playwright, ElementHandle
from dotenv import load_dotenv
from argparse import ArgumentParser
import os
import time
import random
import pandas as pd
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential
import anthropic
from utils import *

load_dotenv()
logger = init_logging()

SPOT_CHECK_CSV = "spot_check_log.csv"


class CovidenceClassifier:
    def __init__(self, output_csv: str, headless: bool, article_limit: int, spot_check_rate: float) -> None:
        self.output_csv: str = output_csv
        self.headless: bool = headless
        self.article_limit: int = article_limit
        self.spot_check_rate: float = spot_check_rate
        self.total_tokens: int = 0
        self.max_tries: int = 0
        self.seen_titles: pd.DataFrame = self.load_existing_titles(
            self.output_csv)
        self.processed_articles: int = self.seen_titles.shape[0]
        self.new_articles: List[Article] = []
        self.spot_check_records: List[Dict[str, Any]] = []
        self.learned_corrections: List[Dict[str, Any]] = load_learned_corrections()
        if self.learned_corrections:
            logger.info(
                f"Loaded {len(self.learned_corrections)} learned correction(s) "
                "from past runs.")
        self.claude_clients: Dict[str, anthropic.Anthropic] = {
            key.strip(): anthropic.Anthropic(api_key=key.strip())
            for key in os.getenv("ANTHROPIC_API_KEY", "").split(",") if key.strip()
        }
        if not self.claude_clients:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set in .env. Add at least one Claude API key.")

    @staticmethod
    def load_existing_titles(csv_path: str) -> pd.DataFrame:
        if not os.path.exists(csv_path):
            return pd.DataFrame(columns=["title", "abstract", "decision", "justification"]).set_index("title")
        return pd.read_csv(csv_path, index_col="title")

    @staticmethod
    def handle_popover(page: Page) -> None:
        try:
            if page.is_visible('button[aria-label="Close popover"]'):
                page.click('button[aria-label="Close popover"]')
        except Exception as e:
            logger.debug(f"Popover close attempt failed: {e}")

    @staticmethod
    def extract_study_elements(top_study: Optional[ElementHandle]) -> Tuple[str, str, str]:
        # Covidence's CSS classes are auto-generated per build (e.g.
        # "webpack-components-core-Card-Card-module__Card") and change on
        # every deploy, so we don't select on those. Instead:
        #   - title: the study card's <h3>
        #   - abstract: reliably the single longest <p> in the card (every
        #     other line — authors, journal, DOI, ref id — is much shorter)
        #   - source_info: the remaining <p> lines, joined together
        try:
            title = top_study.query_selector('h3').inner_text().strip()
            paragraphs = [
                p.inner_text().strip()
                for p in top_study.query_selector_all('p')
            ]
            paragraphs = [p for p in paragraphs if p]
            if not paragraphs:
                return title, "", ""
            abstract = max(paragraphs, key=len)
            source_info = " | ".join(p for p in paragraphs if p != abstract)
            return title, abstract, source_info
        except Exception as e:
            logger.warning(f"Failed to extract study details: {e}")
            return "", "", ""

    @staticmethod
    def vote_on_study(top_study: Optional[ElementHandle], value: str) -> None:
        try:
            buttons = top_study.query_selector_all(
                'button[data-pendo-key="vote-button"]')
            for button in buttons:
                if button.inner_text().strip() == value:
                    button.click(force=True)
                    logger.info(f"Voted '{value}' on study.")
                    return
            logger.warning(f"Could not find a '{value}' vote button.")
        except Exception as e:
            logger.warning(f"Could not vote '{value}' on study: {e}")

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def set_up_browser(self, p: Playwright) -> Tuple[Browser, Page]:
        browser = p.chromium.launch(headless=self.headless)
        page = browser.new_page()

        page.goto(SIGN_IN_URL, timeout=60000)
        page.fill('#session_email', os.getenv("COVID_ID"))
        page.fill('#session_password', os.getenv("COVID_PASSWORD"))
        page.click('input[name="commit"]')
        page.wait_for_load_state("load", timeout=60000)

        page.goto(SCREENING_URL, timeout=60000)
        page.wait_for_load_state("load", timeout=60000)
        return browser, page

    def spot_check(self, title: str, abstract: str, source_info: str,
                   classification: str, justification: str) -> Tuple[str, str]:
        """Pause and show the AI's decision to a human for a random sample of
        articles. Returns the (possibly overridden) classification and
        justification. Runs on the console, so this only makes sense while
        you're watching the terminal (i.e. not a fully unattended run)."""
        print("\n" + "=" * 70)
        print("SPOT-CHECK — review this AI decision before it's submitted")
        print("=" * 70)
        print(f"Title:      {title}")
        print(f"Abstract:   {abstract[:500]}{'...' if len(abstract) > 500 else ''}")
        print(f"Source:     {source_info}")
        print("-" * 70)
        print(f"AI decision: {classification}")
        print(f"Justification: {justification}")
        print("-" * 70)
        choice = input(
            "[Enter]=approve  i=Include  e=Exclude  m=Maybe  "
            "f=flag for your own manual follow-up (still votes as shown above): "
        ).strip().lower()

        choice_map = {"i": "Include", "e": "Exclude", "m": "Maybe"}
        final_classification = classification
        final_justification = justification

        if choice in choice_map:
            final_classification = choice_map[choice]
            # Only a REAL change in decision counts as an override — typing
            # the same letter Claude already landed on (or hitting Enter) is
            # a confirmation, not an override.
            human_action = "overridden" if final_classification != classification else "confirmed"
        elif choice == "f":
            human_action = "flagged_for_manual_review"
        else:
            human_action = "confirmed"

        if human_action == "overridden":
            final_justification = f"[Human override, was '{classification}'] {justification}"

            reason = input(
                "Why? (one line — this gets fed back to Claude so it doesn't "
                "repeat this mistake; Enter to skip): "
            ).strip()
            if reason:
                append_learned_correction(
                    title, classification, final_classification, reason)
                self.learned_corrections.append({
                    "title": title,
                    "ai_decision": classification,
                    "human_decision": final_classification,
                    "reason": reason,
                })
                logger.info(
                    f"Saved as a learned correction — total: {len(self.learned_corrections)}.")

        self.spot_check_records.append({
            "title": title,
            "ai_decision": classification,
            "human_action": human_action,
            "final_decision": final_classification,
        })

        return final_classification, final_justification

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=10))
    def classify_single_article(self, page: Page) -> Tuple[Optional[Article], int]:
        page.reload()
        # Covidence keeps some background network activity running (polling,
        # notifications, etc.) so it never truly reaches "networkidle" — that
        # wait used to time out here every time. page.reload() already blocks
        # until the page's "load" event fires, and the selector wait below is
        # the real signal we need (that a study row actually rendered), so we
        # don't need an extra networkidle wait at all.
        page.wait_for_selector('[data-testclass="study"]', timeout=15000)

        self.handle_popover(page)
        top_study = page.query_selector('[data-testclass="study"]')
        if not top_study:
            logger.info("No study found.")
            return None, 0

        title, abstract, source_info = self.extract_study_elements(top_study)

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
            existing = self.seen_titles.loc[title]
            if isinstance(existing, pd.DataFrame):
                existing = existing.iloc[0]
            logger.info(
                f"Already classified '{title}' as {existing['decision']}")
            vote = decision_map.get(existing["decision"], "Maybe")
            self.vote_on_study(top_study, vote)
            return {
                "title": title,
                "abstract": existing["abstract"],
                "decision": existing["decision"],
                "justification": existing["justification"]
            }, 0

        classification_result, tokens_used = self.claude_decision(
            title, abstract, source_info)

        if not classification_result:
            logger.error("Claude returned no classification. Skipping.")
            return None, 0

        decision = classification_result.classification.value
        justification = classification_result.justification

        # Always spot-check "Maybe" (it's already the AI's least-confident
        # call) plus a random sample of everything else at --spot-check-rate.
        if decision == "Maybe" or random.random() < self.spot_check_rate:
            decision, justification = self.spot_check(
                title, abstract, source_info, decision, justification)

        vote = decision_map.get(decision, "Maybe")
        self.vote_on_study(top_study, vote)

        return {
            "title": title,
            "abstract": abstract,
            "decision": decision,
            "justification": justification
        }, tokens_used

    @limits(calls=100, period=60)
    @sleep_and_retry
    def claude_decision(self, title: str, abstract: str, source_info: str) -> Tuple[Optional[ClassificationResult], int]:
        full_prompt = USER_PROMPT + format_learned_corrections(self.learned_corrections) + f"""
        Title: {title}
        Abstract: {abstract}
        Source Info: {source_info}
        """

        tools = [{
            "name": "submit_classification",
            "description": "Submit the classification and justification for this article.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "classification": {
                        "type": "string",
                        "enum": ["Include", "Exclude", "Maybe"],
                    },
                    "justification": {"type": "string"},
                },
                "required": ["classification", "justification"],
            },
        }]

        for key, client in self.claude_clients.items():
            try:
                response = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    tool_choice={"type": "tool",
                                 "name": "submit_classification"},
                    messages=[{"role": "user", "content": full_prompt}],
                )

                token_usage = getattr(response.usage, "input_tokens", 0) + \
                    getattr(response.usage, "output_tokens", 0)

                tool_block = next(
                    (b for b in response.content if b.type == "tool_use"), None)
                if not tool_block:
                    logger.error(
                        f"No tool-use block from Claude with key ending in {key[-4:]}.")
                    continue

                parsed = ClassificationResult(**tool_block.input)
                logger.info(f"Claude classification: {parsed.classification.value}")
                logger.info(f"Claude justification: {parsed.justification}")
                return parsed, token_usage

            except Exception as e:
                logger.warning(f"API key ending in {key[-4:]} failed: {e}")

        logger.error("All API keys failed.")
        return None, 0

    def save_article(self, article: Article, tokens_used: int) -> None:
        self.new_articles.append(article)
        df = pd.DataFrame.from_records([article], index="title")
        self.seen_titles = pd.concat([self.seen_titles, df])
        self.total_tokens += tokens_used
        self.processed_articles += 1
        self.max_tries = 0

    def save_to_csv(self) -> None:
        if not self.new_articles:
            logger.info("No new articles to save.")
        else:
            df = pd.DataFrame.from_records(self.new_articles, index="title")
            df.to_csv(self.output_csv, mode='a',
                      header=not os.path.exists(self.output_csv), index=True)
            logger.debug("Data saved.")

        if self.spot_check_records:
            df = pd.DataFrame.from_records(self.spot_check_records)
            df.to_csv(SPOT_CHECK_CSV, mode='a',
                      header=not os.path.exists(SPOT_CHECK_CSV), index=False)
            logger.info(
                f"Logged {len(self.spot_check_records)} spot-checks to {SPOT_CHECK_CSV}.")

    def run(self) -> None:
        logger.debug("Starting Covidence classification script...")
        logger.info(
            f"Loaded {self.processed_articles} previously processed articles.")
        logger.info(
            f"Spot-check: every 'Maybe' decision, plus ~{self.spot_check_rate * 100:.0f}% "
            "of Include/Exclude decisions, will pause for your review.")

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
    parser.add_argument("--spot-check-rate", type=float, default=SPOT_CHECK_RATE,
                        help="Fraction (0.0-1.0) of Include/Exclude decisions to pause and confirm yourself, "
                             "e.g. 0.2 = ~20%%. Every 'Maybe' decision is always spot-checked regardless of this.")
    args = parser.parse_args()

    classifier = CovidenceClassifier(
        output_csv=args.output,
        headless=args.headless,
        article_limit=args.articles,
        spot_check_rate=args.spot_check_rate,
    )
    classifier.run()
