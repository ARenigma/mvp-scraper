from playwright.sync_api import sync_playwright
from tqdm import tqdm
from datetime import datetime
from upload_to_drive import upload_to_drive
import csv
import json
import os
import time
import re
import subprocess
import socket
import logging
import structlog
import sys

OUTPUT_FOLDER = "output"

def ensure_output_folder():
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

# Standard logging setup
log_file = f"linkedin_scraper_{datetime.now().strftime('%d%m%Y_%H%M%S')}.log"
logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=logging.INFO,
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

# Environment Var for credentials
USERNAME = os.environ.get("LINKEDIN_USERNAME")
PASSWORD = os.environ.get("LINKEDIN_PASSWORD")

# Hardcoded filter keywords for job title & location
FILTER_KEYWORDS = ["node", "python developer", "php developer", "java", "laravel", "django", "flutter", "iOS developer", "react", "full stack", "frontend", "backend"]
LOCATIONS = ["Bengaluru", "Gurugram"]

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

def extract_job_details(page, location="", keyword=""):
    job_details = {
        "Job Title": "Not Specified",
        "Job City": location,
        "Job Profile": keyword,
        "Company Name": "Not Specified",
        "Job Location": "Not Specified",
        "Workplace Type": "Not Specified",
        "Job Type": "Not Specified",
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

        # Workplace Type
        try:
            insight_texts = page.locator(".job-details-preferences-and-skills__pill").all_text_contents()
            for text in insight_texts:
                if "workplace type is" in text.lower():
                    if "on-site" in text.lower():
                        job_details["Workplace Type"] = "On-site"
                    elif "remote" in text.lower():
                        job_details["Workplace Type"] = "Remote"
                    elif "hybrid" in text.lower():
                        job_details["Workplace Type"] = "Hybrid"
                    break
        except:
            pass

        # Job Type from skill/insight section
        insight_elements = page.locator(".job-details-preferences-and-skills__pill").all()
        for element in insight_elements:
            text = element.text_content().strip()
            #print(f"🔍 Found insight element: {text}")
            lower_text = text.lower()
            if any(jt in lower_text for jt in ["full-time", "part-time", "contract", "internship", "temporary"]):
                job_type_clean = re.sub(r"matches your job preferences.*", "", text, flags=re.IGNORECASE).strip()
                job_details["Job Type"] = job_type_clean
            if "ago" in lower_text:
                job_details["Posted Time"] = text.strip()

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

COOKIES_FILE = "linkedin_cookies.json"

def load_linkedin_cookies(context):
    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE, "r") as f:
                cookies = json.load(f)
            context.add_cookies(cookies)
            logger.info("🍪 Cookies loaded from file.")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load cookies: {e}")

def save_linkedin_cookies(context):
    try:
        cookies = context.cookies()
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f, indent=2)
        logger.info("💾 Cookies saved.")
    except Exception as e:
        logger.error(f"❌ Failed to save cookies: {e}")

USERNAME = os.environ.get("LINKEDIN_USERNAME")
PASSWORD = os.environ.get("LINKEDIN_PASSWORD")

def login_linkedin(context, page):
    # Step 1: Try visiting feed directly (after loading cookies)
    page.goto("https://www.linkedin.com/feed/", timeout=10000)

    if "feed" in page.url:
        logger.info("✅ Logged in via cookies!")
        return

    # Step 2: Fallback to manual login
    logger.warning("🔐 Cookies failed or expired. Logging in manually...")
    page.goto("https://www.linkedin.com/login")

    try:
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        # Step 3: Check for OTP screen
        if page.locator("input[name='pin']").is_visible():
            logger.warning("🔐 2FA detected! Waiting for OTP input...")
            otp = input("📲 Enter the OTP sent to your device: ")
            page.fill("input[name='pin']", otp)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            logger.info("✅ 2FA completed.")

        # Step 4: Verify login worked
        page.goto("https://www.linkedin.com/feed/")
        if "feed" in page.url:
            logger.info("✅ Logged in successfully.")
            save_linkedin_cookies(context)
        else:
            logger.warning("⚠️ Login may have failed. Check manually.")

    except Exception as e:
        logger.exception(f"❌ Login or 2FA failed: {e}")


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
    # log the selector we're trying to scroll
    logger.debug(f"📌 Scroll selector detected: {scroll_selector}")


    if not scroll_selector:
        logger.critical("❌ Scroll container NOT found.")
        return False  # Signal failure to the caller

    #print(f"✅ Using scroll container: {scroll_selector}")

    last_card_count = -1
    stagnant_scrolls = 0
    max_stagnant_scrolls = 5

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

# Make Sure Screenshot Folder Exists
if not os.path.exists("screenshots"):
    os.makedirs("screenshots")


def scrape_jobs(keyword, location, max_count=5):
    jobs = []
    start = 0
    global_job_index = 1
    consecutive_empty_pages = 0
    max_empty_pages = 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # Headless browser
        context = browser.new_context(storage_state="./storage_state.json")

        
        # Load cookies (if exist)
        load_linkedin_cookies(context) 
        
        page = context.new_page()
        
        login_linkedin(context, page)

        try:
            with tqdm(total=max_count if max_count else 0, desc="Processing Jobs", unit="job") as progress:
                while True:
                    if max_count is not None and len(jobs) >= max_count:
                        logger.info("✅ Reached max job count.")
                        break

                    if consecutive_empty_pages >= max_empty_pages:
                        logger.info(f"⚠️ {consecutive_empty_pages} empty pages in a row. Moving on.")
                        break

                    paged_url = f"https://www.linkedin.com/jobs/search/?keywords={keyword}&location={location}&start={start}&f_TPR=r{time_filter}&f_WT=1%2C1%2C3"
                    logger.info(f"🌐 Visiting: {paged_url}")
                    try:
                       page.goto(paged_url, wait_until="domcontentloaded", timeout=60000)
                       page.wait_for_selector(".job-card-container--clickable", timeout=10000)
                       page.wait_for_timeout(2000)
                    except Exception as e:
                       screenshot_path = f"screenshots/goto_error_{keyword}_{location}.png"
                       
                       from upload_to_drive import upload_screenshot_to_drive
                       page.screenshot(path=screenshot_path, full_page=True)
                       upload_screenshot_to_drive(screenshot_path)

                       logger.critical(f"❌ Goto timeout or page error. Screenshot saved: {screenshot_path}")
                       logger.exception(e)
                       return []


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

                            if not re.search(rf"\b{re.escape(keyword.lower())}\b", job_title):
                                logger.debug(f"❌ No match for keyword '{keyword}' — skipping card {i + 1}")
                                continue

                            logger.info(f"👉 Clicking job card #{i + 1}: '{job_title}'")
                            card.click()
                            page.wait_for_timeout(3000)

                            job = extract_job_details(page, location, keyword)
                            jobs.append(job)
                            logger.info(f"✅ [{global_job_index}] {job['Job Title']} at {job['Company Name']}")
                            global_job_index += 1
                            progress.update(1)
                            match_count += 1

                        except Exception as e:
                            logger.exception(f"❌ Error processing job card #{i + 1}: {e}")
                            try:
                                page.screenshot(path=f"error_card_{i + 1}.png")
                            except:
                                pass

                    # NEW: Handle empty page detection based on job titles
                    if len(seen_titles) == 0:
                        consecutive_empty_pages += 1
                        logger.info(f"⚠️ No new job titles seen — possible end of listings ({consecutive_empty_pages}/{max_empty_pages})")
                    else:
                        consecutive_empty_pages = 0

                    start += 25

        finally:
            context.close()
            browser.close()

    return jobs

OUTPUT_FOLDER = "output"

def save_jobs(jobs, filename=None):
    """Save jobs to CSV, overwriting any existing file"""
    if not jobs:
        logger.warning("⚠️ No jobs to save.")
        return filename
    
    ensure_output_folder()  # Ensure output/ exists

    if filename is None:
        date_str = datetime.now().strftime("%d-%m-%Y")
        filename = os.path.join(OUTPUT_FOLDER, f"linkedin_jobs_{date_str}.csv")
    
    keys = jobs[0].keys()
    with open(filename, "w", newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(jobs)
    
    logger.info(f"💾 Saved {len(jobs)} jobs to {filename}")
    return filename

def save_jobs_periodic(jobs, interval_minutes=10):
    """Track time and periodically save jobs to CSV"""
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


if __name__ == "__main__":
    time_filter = 86400  # Past 1 day
    job_count = None     # No cap per combo

    all_jobs = []

    save_interval = 10  # Save every 10 minutes
    filename = None

    try:
        for keyword in FILTER_KEYWORDS:
            for location in LOCATIONS:
                logger.info(f"\n🔎 Scraping for keyword '{keyword}' in '{location}'")
                jobs = scrape_jobs(keyword, location, max_count=job_count)
                all_jobs.extend(jobs)
                
                # Periodically save after each keyword-location combination
                filename = save_jobs_periodic(all_jobs, save_interval)
        
        # Final save at the end
        filename = save_jobs(all_jobs, filename)
        
        # Rename the file to indicate complete scraping
        rename_file_complete(filename)
        logger.info("✅ Scraping completed successfully!")
        
    except KeyboardInterrupt:
        logger.warning("\n\n⚠️ Stopping the bot due to keyboard interrupt (Ctrl+C)...")
        if all_jobs:
            save_jobs(all_jobs, filename)
            logger.info("✅ CSV saved successfully (without COMPLETE marker)!")
        else:
            logger.info("ℹ️ No jobs were collected to save.")
    except Exception as e:
        logger.exception(f"\n❌ An error occurred: {e}")
        if all_jobs:
            save_jobs(all_jobs, filename)
            logger.info("✅ CSV saved successfully despite error (without COMPLETE marker)!")


filename = save_jobs(all_jobs, filename)
rename_file_complete(filename)
upload_to_drive(filename)