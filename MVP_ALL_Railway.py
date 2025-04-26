from playwright.sync_api import sync_playwright
from tqdm import tqdm
from datetime import datetime
from upload_to_drive import upload_to_drive
import pandas as pd
import os
import json
import time
import re
import subprocess
import socket
import logging
import structlog
import sys
import threading

# Create logs/ folder if it doesn't exist
LOG_FOLDER = "logs"
os.makedirs(LOG_FOLDER, exist_ok=True)

# Define timestamped log file path
log_file = os.path.join(LOG_FOLDER, f"linkedin_scraper_{datetime.now().strftime('%d%m%Y_%H%M%S')}.log")

# Log to both file and terminal
logging.basicConfig(
    format="%(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_file, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Structlog configuration
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,                  # Add log level to event dict
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.dev.set_exc_info,                      # show exception stack if present
        structlog.dev.ConsoleRenderer(colors=True),     # pretty console output with color
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

def launch_browser_headless():
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-setuid-sandbox",
            "--disable-extensions",
            "--disable-blink-features=AutomationControlled",
        ]
    )
    context = browser.new_context()
    page = context.new_page()
    return page, context, browser, playwright

# Hardcoded filter keywords for job title & locationPY py"flutter","react developer","full stack developer","frontend developer", "backend developer","node developer", "java developer",
FILTER_KEYWORDS = [ "Python developer","PHP DEVELOPER"]
LOCATIONS = ["Gurugram"]

def extract_text(locator, selector, default="Not Specified"):
    try:
        return locator.locator(selector).text_content(timeout=3000).strip()
    except:
        return default

def normalize(text):
    return re.sub(r'[^a-zA-Z0-9]', '', text.lower())

def extract_href(locator, selector, default=""):
    try:
        href = locator.locator(selector).get_attribute("href", timeout=3000)
        if href:
            return href if href.startswith("http") else "https://www.linkedin.com" + href
        return default
    except:
        return default

def find_job_top_card(page):
    """Finds the top card container dynamically based on the presence of muted buttons like On-site, Full-time, etc."""
    try:
        divs = page.locator("div").all()
        for div in divs:
            div_text = div.inner_text().lower()
            if (
                ("full-time" in div_text or "on-site" in div_text or "remote" in div_text or "hybrid" in div_text)
                and div.locator("button.artdeco-button--muted").count() > 0
            ):
                logger.info("✅ Dynamically found the job top card container.")
                return div
    except Exception as e:
        logger.warning(f"⚠️ Failed to dynamically locate job top card: {e}")
    return None

def extract_workplace_type_job_type_and_job_budget(page):
    """Extract Workplace Type and Job Type dynamically without depending on hardcoded div names."""
    workplace_type = None
    job_type = None
    job_budget = None


    try:
        top_card = find_job_top_card(page)
        if not top_card:
            logger.warning("⚠️ Could not find dynamic top card container for job details.")
            return workplace_type, job_type

        buttons = top_card.locator("button.artdeco-button--muted")
        count = buttons.count()

        for i in range(count):
            try:
                text = buttons.nth(i).inner_text(timeout=3000).strip().lower()
                logger.debug(f"🔎 Button {i}: {text}")

                if text in ["on-site", "remote", "hybrid"]:
                    workplace_type = text.title()
                elif text in ["full-time", "part-time", "contract", "internship", "temporary", "volunteer", "other"]:
                    job_type = text.title()
                
                 # Check for salary format like "₹600K/yr - ₹2M/yr"
                elif "₹" in text or "$" in text or "€" in text or "£" in text:
                    job_budget = buttons.nth(i).inner_text(timeout=3000).strip()    
            except Exception as e:
                logger.debug(f"⚠️ Failed reading button {i}: {e}")

    except Exception as e:
        logger.warning(f"⚠️ Failed to extract job type fields: {e}")

    return workplace_type, job_type, job_budget

def extract_job_details(page, location="", keyword=""):
    job_details = {
        "Job Title": "Not Specified",
        "Job City": location,
        "Job Profile": keyword,
        "Company Name": "Not Specified",
        "Job Location": "Not Specified",
        "Workplace Type": "Not Specified",
        "Job Type": "Not Specified",
        "Job Budget": "Not Specified",
        "Posted Time": "Not Specified",
        "Total Employees": "Not Specified",
        "Industry": "Not Specified",
        "Company LinkedIn URL": "",
        "Job URL": "",
        "Hiring Manager URL": "",
    }

    try:
        # Basic job info
        job_details["Job Title"] = extract_text(page, ".job-details-jobs-unified-top-card__job-title")
        job_details["Company Name"] = extract_text(page, ".job-details-jobs-unified-top-card__company-name")
        raw_location = extract_text(page, ".job-details-jobs-unified-top-card__primary-description-container")
        cleaned_location = re.split(r"·", raw_location)[0].strip()
        job_details["Job Location"] = cleaned_location.split(",")[0].strip()

        # Job Posted Time
        try:
            posted_element = page.locator(".job-details-jobs-unified-top-card__primary-description-container strong span")
            job_details["Posted Time"] = posted_element.text_content(timeout=3000).strip()
        except:
            pass

        # Workplace, Job Type, and Job Budget (using dynamic method)
        workplace_type, job_type, job_budget = extract_workplace_type_job_type_and_job_budget(page)
        if workplace_type:
            job_details["Workplace Type"] = workplace_type
        if job_type:
            job_details["Job Type"] = job_type
        if job_budget:
            job_details["Job Budget"] = job_budget    

        # Job URL        
        try:
            job_url = page.locator("h1.t-24 a").first.get_attribute("href")
            if job_url: 
                job_url = job_url.split("?")[0]
                if not job_url.startswith("https://"):
                    job_url = "https://www.linkedin.com" + job_url
            job_details["Job URL"] = job_url
        except:
            job_details["Job URL"] = ""

        # Company LinkedIn URL    
        company_link_elem = page.locator("div.job-details-jobs-unified-top-card__company-name")
        job_details["Company LinkedIn URL"] = extract_href(company_link_elem, "a")

        # Sector / Industry
        try:
            industry_text = page.locator('div.t-14.mt5').first.text_content().strip()
            lines = [line.strip() for line in industry_text.split('\n') if line.strip()]
            job_details["Industry"] = lines[0] if lines else ""
        except:
            pass

        # Total Employees
        try:
            employees_text = page.locator("section[data-view-name='job-details-about-company-module'] span.jobs-company__inline-information").first.text_content().strip()
            employee_range = employees_text.replace("employees", "").strip()
            
            if employee_range:
                employee_range = str(employee_range).strip()

            job_details["Total Employees"] = f'="{employee_range}"'
        except:
            job_details["Total Employees"] = ""

        # Hiring Manager    
        try:
            hirer_card = page.locator("div.hirer-card__hirer-information")
            job_details["Hiring Manager URL"] = extract_href(hirer_card, "a")
        except Exception as e:
            logger.error(f"❌ Error extracting Hiring Manager URL: {e}")
            job_details["Hiring Manager URL"] = ""

    except Exception as e:
        logger.exception(f"❌ Error extracting job details: {e}")

    return job_details

def login_linkedin(page, context, browser):
    """Check if logged into LinkedIn. Retry if feed fails. Context reset fallback if needed."""
    
    USERNAME = os.getenv("LINKEDIN_USERNAME")
    PASSWORD = os.getenv("LINKEDIN_PASSWORD")

    if not USERNAME or not PASSWORD:
        logger.critical("❌ LINKEDIN_USERNAME or LINKEDIN_PASSWORD environment variables not set!")
        raise Exception("Missing LinkedIn credentials. Set them in Railway environment variables.")
    
    try:
        logger.info("🔐 Checking LinkedIn login status...")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_selector(".global-nav__me-photo", timeout=4000)
        logger.info("✅ Already logged in to LinkedIn!")
        return page, context

    except Exception as e:
        logger.warning(f"⚠️ Feed check failed: {e}")
        logger.info("⏳ Cooling down and retrying with new page...")
        page.wait_for_timeout(10000)  # cooldown

        try:
            fallback_page = context.new_page()
            fallback_page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=10000)
            fallback_page.wait_for_selector(".global-nav__me-photo", timeout=4000)
            logger.info("✅ Logged in successfully after fallback page retry.")
            return fallback_page, context

        except Exception as fallback_error:
            logger.warning(f"⚠️ Fallback page feed check also failed: {fallback_error}")
            logger.info("♻️ Resetting context and trying fresh login...")

            try:
                context.close()
            except:
                logger.warning("⚠️ Context close failed during reset.")

            context = browser.new_context()
            new_page = context.new_page()
            
            try:
                new_page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=10000)
                logger.info("🔐 Filling LinkedIn credentials manually...")
                new_page.fill("#username", USERNAME)
                new_page.fill("#password", PASSWORD)
                new_page.click("button[type='submit']")
                new_page.wait_for_load_state("networkidle")
                logger.info("✅ Logged in to LinkedIn after full context reset.")
                return new_page, context

            # If even this fails, you could optionally raise exception
            except Exception as login_exception:
                logger.exception(f"⚠️ Login failed or already logged in: {e}")
                raise login_exception  # Stop everything, critical failure   

def scroll_job_list_panel(page, min_cards=25, max_scrolls=20, delay=4):
    logger.info(f"📜 Scrolling until at least {min_cards} job cards are visible...")

    # Dynamically detect the scrollable container holding job cards
    scroll_selector = page.evaluate("""
        () => {
            const all = Array.from(document.querySelectorAll("div"));
            for (let el of all) {
                if (el.scrollHeight > el.clientHeight && el.querySelector('.job-card-container--clickable')) {
                    const cls = el.getAttribute("class");
                    if (cls) {
                        const firstClass = cls.split(" ")[0].replace(/[^a-zA-Z0-9_-]/g, "");
                        return `div[class*="${firstClass}"]`;
                    }
                }
            }
            return null;
        }
    """)


    if not scroll_selector:
        logger.critical("❌ Scroll container NOT found.")
        return False  # Signal failure to the caller

    #print(f"✅ Using scroll container: {scroll_selector}")

    last_card_count = -1
    stagnant_scrolls = 0
    max_stagnant_scrolls = 3

    for i in range(max_scrolls):
        job_cards = page.locator(".job-card-container--clickable")
        total_cards = job_cards.count()
        logger.debug(f"🔄 Scroll {i + 1}: {total_cards} job cards found.")

        if total_cards >= min_cards:
            logger.info(f"✅ Loaded {total_cards} job cards.")
            break

        if total_cards == last_card_count:
            stagnant_scrolls += 1
            logger.warning(f"⚠️ No new cards detected ({stagnant_scrolls}/{max_stagnant_scrolls})")
            if stagnant_scrolls >= max_stagnant_scrolls:
                logger.info("🛑 Stopping — content stopped loading.")
                break
        else:
            stagnant_scrolls = 0
            last_card_count = total_cards

        # Perform smooth scroll in steps
        for _ in range(10):  # smaller steps for smoother loading
            page.evaluate("""
                (selector) => {
                    const el = document.querySelector(selector);
                    if (el) el.scrollBy(0, 300);
                }
            """, scroll_selector)
            page.wait_for_timeout(400)  # pause between scrolls

        page.wait_for_timeout(delay * 1000)

    else:
        logger.warning(f"⚠️ Max scrolls reached but only {total_cards} job cards found.")

    return True  # signal success to the caller    

def scrape_jobs(page, context, keyword, location, max_count=5):
    jobs = []
    start = 0                  # pagination offset, increases by 25 for each page
    global_job_index = 1
    consecutive_empty_pages = 0
    max_empty_pages = 2        # After this many empty pages, move to next keyword

    with tqdm(total=max_count if max_count else 0, desc="Processing Jobs", unit="job") as progress:
        while True:
            if max_count is not None and len(jobs) >= max_count:
                logger.info("✅ Reached max job count.")
                break

            if consecutive_empty_pages >= max_empty_pages:
                logger.info(f"⚠️ {consecutive_empty_pages} empty pages in a row. Moving on.")
                break

            paged_url = f"https://www.linkedin.com/jobs/search/?keywords={keyword}&location={location}&start={start}&f_TPR=r{time_filter}&f_WT=1%2C1%2C3"
            
            # ✅ Enhanced navigation + refresh fallback + smart screenshot
            nav_attempts = 0
            max_nav_retries = 1
            nav_success = False

            while nav_attempts <= max_nav_retries:
                try:
                    logger.info(f"🌐 Visiting: {paged_url} (Attempt {nav_attempts + 1})")
                    page.goto(paged_url, wait_until="domcontentloaded", timeout=10000)

                    # Ensure cards are loaded
                    if not page.locator(".job-card-container--clickable").first.is_visible(timeout=5000):
                        raise Exception("Job card panel not visible after page load.")
                    
                    # ✅ After successful load, extra check for no results
                    try:
                        body_text = page.inner_text("body").lower()
                        if "no matching jobs found" in body_text or "try different filters" in body_text or "search again" in body_text:
                            logger.info(f"✅ No matching jobs found at offset {start}. End of results.")
                            break  # 🔥 Exit scrape loop immediately
                    except Exception as e:
                        logger.warning(f"⚠️ Could not check for end of results: {e}")

                    nav_success = True
                    break

                except Exception as e:
                    logger.warning(f"⚠️ Navigation/visibility failed on attempt {nav_attempts + 1}: {e}")

                    # Try refresh after first failure
                    if nav_attempts == 0:
                        logger.info("🔄 Refreshing page and retrying...")
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=7000)
                        except:
                            logger.warning("⚠️ Page reload failed.")

                    page.wait_for_timeout(5000)
                    nav_attempts += 1

            if not nav_success:
                logger.error(f"❌ Navigation failed after {max_nav_retries + 1} attempts: {paged_url}")
                try:
                    no_results_text = page.inner_text("body").lower()
                    if (
                        "no matching jobs" in no_results_text
                        or "try different keywords" in no_results_text
                        or page.locator(".job-card-container--clickable").count() == 0
                    ):
                        logger.info("⛔ Reached end of results — skipping screenshot.")
                    else:
                        logger.info("📸 Navigation failed unexpectedly — taking screenshot.")
                        screenshot_path = os.path.join(SCREENSHOT_FOLDER, f"nav_uncertain_{keyword}_{location}_{start}.png")
                        page.screenshot(path=screenshot_path)

                except Exception as e:
                    logger.warning(f"⚠️ Could not determine end-of-results status: {e}")
                    try:
                        screenshot_path = os.path.join(SCREENSHOT_FOLDER, f"nav_uncertain_{keyword}_{location}_{start}.png")
                        page.screenshot(path=screenshot_path)
                    except:
                        pass

                break

            if not scroll_job_list_panel(page, min_cards=25, delay=4):
                logger.warning("⚠️ Scrolling failed. Skipping.")
                consecutive_empty_pages += 1
                start += 25
                continue

            try:
                page.wait_for_selector(".job-card-container--clickable", timeout=5000)
            except:
                logger.warning("⚠️ Job cards not found. Skipping page.")
                consecutive_empty_pages += 1
                start += 25
                continue

            job_cards = page.locator(".job-card-container--clickable")
            total_cards = job_cards.count()
            logger.info(f"📄 Found {total_cards} job cards.")

            if total_cards == 0:
                logger.warning("🚫 No job cards found.")
                consecutive_empty_pages += 1
                start += 25
                continue

            logger.info("✅ Beginning to process job cards...")
            match_count = 0
            seen_titles = set()  # Track visible job titles seen on this page

            for i in range(total_cards):
                if max_count is not None and len(jobs) >= max_count:
                    logger.info("✅ Reached max job count.")
                    return jobs

                try:
                    card = job_cards.nth(i)

                    if not card.is_visible(timeout=3000):
                        logger.debug(f"⚠️ Card {i} not visible. Skipping.")
                        continue

                    try:
                        card_text = card.inner_text(timeout=3000)
                        logger.info(f"🔍 Job card #{i + 1} content:\n{card_text}")

                        if not card_text or len(card_text.strip()) == 0:
                            logger.warning(f"⚠️ Card {i} had no visible text. Skipping.")
                            continue

                        job_title_lines = card_text.strip().split("\n")
                        job_title = re.sub(r"\s+", " ", job_title_lines[0]).strip().lower()
                        seen_titles.add(job_title)

                    except Exception as e:
                        logger.warning(f"⚠️ Could not extract visible title for card {i}: {e}")
                        continue

                    logger.debug(f"🔍 Matching '{keyword}' in title: '{job_title}'")

                    # Multi-word substring matching
                    current_keyword = keyword.lower()
                    keyword_words = re.split(r'\s+', current_keyword)  # Split keyword into words
                    all_words_present = True

                    for word in keyword_words:
                        if word not in job_title:
                            all_words_present = False
                            break

                    if not all_words_present:
                        logger.debug(f"❌ No match for keyword '{keyword}' — skipping card {i + 1}")
                        continue

                    logger.info(f"👉 Match Found - Clicking job card #{i + 1}: '{job_title}'")
                    card.click()
                    page.wait_for_timeout(5000)

                    job = extract_job_details(page, location, keyword)
                    jobs.append(job)
                    logger.info(f"✅ [{global_job_index}] {job['Job Title']} at {job['Company Name']}")
                    global_job_index += 1
                    progress.update(1)
                    match_count += 1

                except Exception as e:
                    logger.exception(f"❌ Error processing job card #{i + 1}: {e}")
                    try:
                        screenshot_path = os.path.join(SCREENSHOT_FOLDER, f"nav_uncertain_{keyword}_{location}_{start}.png")
                        page.screenshot(path=screenshot_path)
                    except:
                        pass

            # NEW: Handle empty page detection based on job titles
            if len(seen_titles) == 0:
                consecutive_empty_pages += 1
                logger.info(f"⚠️ No new job titles seen — possible end of listings ({consecutive_empty_pages}/{max_empty_pages})")
            else:
                consecutive_empty_pages = 0

            start += 25
    
    return jobs

RESUME_FILE = "resume_state.json"

def save_resume_point(combo_counter, keyword, location):
    """Save the current scraping state to resume later."""
    state = {
        "combo_counter": combo_counter,
        "keyword": keyword,
        "location": location
    }
    try:
        with open(RESUME_FILE, "w") as f:
            json.dump(state, f)
        logger.info(f"💾 Saved resume point at combo #{combo_counter}: '{keyword}' in '{location}'")
    except Exception as e:
        logger.warning(f"⚠️ Failed to save resume point: {e}")

def load_resume_point():
    """Load previously saved resume point if available."""
    if os.path.exists(RESUME_FILE):
        try:
            with open(RESUME_FILE, "r") as f:
                state = json.load(f)
                logger.info(f"🔄 Found resume point: combo #{state['combo_counter']} at '{state['keyword']}' / '{state['location']}'")
                return state
        except Exception as e:
            logger.warning(f"⚠️ Failed to load resume point: {e}")
    return None

def delete_resume_point():
    """Delete resume file after complete scraping."""
    if os.path.exists(RESUME_FILE):
        try:
            os.remove(RESUME_FILE)
            logger.info("🧹 Deleted resume file after successful complete scraping.")
        except Exception as e:
            logger.warning(f"⚠️ Failed to delete resume file: {e}")

def handle_resume_decision():
    """If resume file exists, ask user if they want to resume or start fresh, with timeout."""
    if os.path.exists(RESUME_FILE):
        user_choice = {"answer": None}

        def ask_input():
            choice = input("\n🛑 Previous incomplete scrape detected.\nWould you like to (R)esume or (F)resh start? [R/F] (auto-fresh in 10s): ").strip().lower()
            user_choice["answer"] = choice

        input_thread = threading.Thread(target=ask_input)
        input_thread.daemon = True
        input_thread.start()
        input_thread.join(timeout=10)

        choice = user_choice["answer"]

        if choice == "r":
            logger.info("🔄 Resuming previous scrape...")
            return load_resume_point()
        elif choice == "f":
            logger.info("🧹 Starting fresh as selected by user...")
            delete_resume_point()
            return None
        else:
            logger.warning("⚡ No input received — starting fresh automatically after 10 seconds.")
            delete_resume_point()
            return None
    else:
        return None

SCREENSHOT_FOLDER = "screenshots"
def ensure_screenshot_folder():
    if not os.path.exists(SCREENSHOT_FOLDER):
        os.makedirs(SCREENSHOT_FOLDER)

OUTPUT_FOLDER = "output"
def ensure_output_folder():
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

def save_jobs(jobs, filename=None):
    """Save jobs to XLSX"""
    if not jobs:
        logger.warning("⚠️ No jobs to save.")
        return filename
    
    ensure_output_folder()  # Ensure output/ exists

    # Create dated folder inside output
    date_folder = datetime.now().strftime("%Y-%m-%d")
    full_output_dir = os.path.join(OUTPUT_FOLDER, date_folder)
    os.makedirs(full_output_dir, exist_ok=True)

    if filename is None:
        timestamp = datetime.now().strftime("%d-%m-%Y_%H%M")
        filename = os.path.join(full_output_dir, f"linkedin_jobs_{timestamp}.xlsx")
    
    df = pd.DataFrame(jobs)
    df.to_excel(filename, index=False, engine="openpyxl")
    
    logger.info(f"💾 Saved {len(jobs)} jobs to {filename}")
    return filename

def save_jobs_periodic(jobs, interval_minutes=10):
    """Track time and periodically save jobs"""
    current_time = time.time()
    
    # Initialize static variables if needed
    if not hasattr(save_jobs_periodic, "last_save_time"):
        save_jobs_periodic.last_save_time = current_time
        save_jobs_periodic.filename = None
    
    # If enough time has passed or it's the first save
    if current_time - save_jobs_periodic.last_save_time >= interval_minutes * 60:
        if jobs:
            save_jobs_periodic.filename = save_jobs(jobs, save_jobs_periodic.filename)
            logger.info(f"⏱️ Auto-saved at {datetime.now().strftime('%H:%M:%S')}")
            save_jobs_periodic.last_save_time = current_time
        return save_jobs_periodic.filename
    
def rename_file_complete(filename):
    """Rename the file to indicate the scraping completed successfully"""
    if not filename or not os.path.exists(filename):
        return
        
    base, ext = os.path.splitext(filename)
    new_filename = f"{base}_COMPLETE{ext}"
    
    try:
        os.rename(filename, new_filename)
        logger.info(f"✅ Renamed file to {new_filename} to indicate complete scraping")
        return new_filename
    except Exception as e:
        logger.exception(f"❌ Failed to rename file: {e}")
        return filename    

def get_time_filter():
    days = input("Enter number of days (e.g., 1 for past 24 hours): ")
    try:
        return int(days) * 86400
    except:
        return 86400

def get_job_count():
    user_input = input("Enter the number of jobs to scrape (leave blank for all available): ")
    if user_input.strip().isdigit():
        return int(user_input.strip())
    else:
        return None  # Run indefinitely until no more jobs are found

def ensure_page_alive(page, context, browser, combo_counter=None, revalidate_every=5):
    """Ensure page and context are alive. Revalidate login every N combos."""
    try:
        if page.is_closed():
            logger.warning("⚠️ Page was closed. Reopening a new one.")
            page = context.new_page()

        if combo_counter is not None and combo_counter % revalidate_every == 0:
            logger.info(f"🔁 Session checkpoint at combo #{combo_counter} — validating login")
            page, context = login_linkedin(page, context, browser)

    except Exception as e:
        logger.error(f"❌ Unexpected error while ensuring page/context health: {e}")
        try:
            context.close()
        except:
            pass
        context = browser.new_context()
        page = context.new_page()
        page, context = login_linkedin(page, context, browser)

    return page, context

if __name__ == "__main__":
    time_filter = 86400  # Past 1 day
    job_count = None     # No cap per combo

    all_jobs = []
    save_interval = 10  # Save every 10 minutes
    filename = None
    ensure_screenshot_folder()

    try: 
        page, context, browser, playwright = launch_browser_headless()
        page, context = login_linkedin(page, context, browser)

        resume_state = handle_resume_decision()
        combo_counter = 0
        resumed = False

        for keyword in FILTER_KEYWORDS:
            for location in LOCATIONS:
                combo_counter += 1

                if resume_state and not resumed:
                    if (
                        combo_counter < resume_state["combo_counter"]
                        or (combo_counter == resume_state["combo_counter"]
                            and (keyword != resume_state["keyword"] or location != resume_state["location"]))
                    ):
                        logger.info(f"⏭️ Skipping combo #{combo_counter}: '{keyword}' in '{location}' (already completed)")
                        continue
                    else:
                        logger.info(f"🔄 Resuming from combo #{combo_counter}: '{keyword}' in '{location}'")
                        resumed = True
                
                current_keyword = keyword
                current_location = location

                logger.info(f"\n🔎 Scraping for keyword '{keyword}' in '{location}'")

                page, context = ensure_page_alive(page, context, browser, combo_counter, revalidate_every=5)
                
                try:
                    jobs = scrape_jobs(page, context, keyword, location, max_count=job_count)
                    all_jobs.extend(jobs)
            
                    # Periodically save after each keyword-location combination
                    filename = save_jobs_periodic(all_jobs, save_interval)

                    # 💾 Save successful resume point after combo is done
                    save_resume_point(combo_counter, keyword, location)
                
                except Exception as combo_error:
                    logger.exception(f"❌ Error scraping combo #{combo_counter}: '{current_keyword}' in '{current_location}'. Skipping to next combo...")
                    continue  # or break or continue depending on what you want    
    
        # Final save at the end
        filename = save_jobs(all_jobs, filename)
        
        # Rename the file to indicate complete scraping
        rename_file_complete(filename)

        # Now delete the resume file
        delete_resume_point()
        logger.info("✅ Scraping completed successfully!")        
        
    except KeyboardInterrupt:
        logger.warning("\n\n⚠️ Stopping the bot due to keyboard interrupt (Ctrl+C)...")
        if all_jobs:
            save_jobs(all_jobs, filename)
            logger.info("✅ Jobs saved successfully (without COMPLETE marker)!")
        else:
            logger.info("ℹ️ No jobs were collected to save.")
    except Exception as e:
        logger.exception(f"\n❌ An error occurred: {e}")
        if all_jobs:
            save_jobs(all_jobs, filename)
            logger.info("✅ Jobs saved successfully despite error (without COMPLETE marker)!")

filename = save_jobs(all_jobs, filename)
rename_file_complete(filename)
upload_to_drive(filename)