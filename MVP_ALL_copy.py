from playwright.sync_api import sync_playwright
from tqdm import tqdm
from datetime import datetime
import csv
import os
import time
import re
import subprocess
import socket

# Hardcoded credentials
USERNAME = "you@example.com"
PASSWORD = "23ranjan"

# Hardcoded filter keywords for job title & location
FILTER_KEYWORDS = ["node", "python", "php", "java", "laravel", "django", "kafka", "flutter", "iOS developer", "react", "full stack", "frontend", "backend"]
LOCATIONS = ["Gurgaon", "Delhi", "Gurugram", "Noida"]

def is_chrome_running(port=9222):
    """Check if Chrome with remote debugging is already running."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(('localhost', port)) == 0

def launch_chrome_debug():
    if is_chrome_running():
        print("🟢 Chrome with debugging already running.")
        return

    bat_path = r"C:\Users\Admin\Desktop\LinkedIn_Scraper\chrome local instance (STEP-1).bat"  # UPDATE this path
    try:
        subprocess.Popen([bat_path], shell=True)
        print("🚀 Chrome debug session launched.")
        time.sleep(5)
    except Exception as e:
        print(f"❌ Failed to launch Chrome: {e}")

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

def extract_job_details(page, keyword=""):
    job_details = {
        "Job Title": "Not Specified",
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
            print(f"❌ Error extracting Hiring Manager URL: {e}")
            job_details["Hiring Manager URL"] = ""

    except Exception as e:
        print(f"❌ Error extracting job details: {e}")

    return job_details

def login_linkedin(page):
    page.goto("https://www.linkedin.com/feed/", timeout=5000)

    # If redirected to feed, you're already logged in
    if "feed" in page.url:
        print("✅ Already logged in to LinkedIn!")
        return

    # Otherwise, go to login and fill credentials
    print("🔐 Logging in to LinkedIn...")
    page.goto("https://www.linkedin.com/login")
    try:
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
        print("✅ Logged in to LinkedIn")
    except Exception as e:
        print(f"⚠️ Login failed or already logged in: {e}")

def scroll_job_list_panel(page, min_cards=25, max_scrolls=20, delay=4):
    print(f"📜 Scrolling until at least {min_cards} job cards are visible...")

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
        print("❌ Scroll container NOT found.")
        return

    #print(f"✅ Using scroll container: {scroll_selector}")

    last_card_count = -1
    stagnant_scrolls = 0
    max_stagnant_scrolls = 5

    for i in range(max_scrolls):
        job_cards = page.locator(".job-card-container--clickable")
        total_cards = job_cards.count()
        print(f"🔄 Scroll {i + 1}: {total_cards} job cards found.")

        if total_cards >= min_cards:
            print(f"✅ Loaded {total_cards} job cards.")
            break

        if total_cards == last_card_count:
            stagnant_scrolls += 1
            print(f"⚠️ No new cards detected ({stagnant_scrolls}/{max_stagnant_scrolls})")
            if stagnant_scrolls >= max_stagnant_scrolls:
                print("🛑 Stopping — content stopped loading.")
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
        print(f"⚠️ Max scrolls reached but only {total_cards} job cards found.")

def scrape_jobs(keyword, location, max_count=5):
    jobs = []
    start = 0  # pagination offset, increases by 25 for each page
    global_job_index = 1  # Global counter across pages

    launch_chrome_debug()  # ✅ THIS STARTS CHROME BEFORE CONNECTING TO CDP

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()

        login_linkedin(page)

        try:
            with tqdm(total=max_count if max_count else 0, desc="🔍 Scraping Jobs", unit="job") as progress:
                while True:
                    if max_count is not None and len(jobs) >= max_count:
                        print("✅ Reached max job count.")
                        break
                    seen_indices = set()
                    paged_url = f"https://www.linkedin.com/jobs/search/?keywords={keyword}&location={location}&start={start}&f_TPR=r{time_filter}&f_WT=1%2C1%2C3"
                    print(f"🌐 Visiting: {paged_url}")
                    page.goto(paged_url)
                    page.wait_for_timeout(10000)  # Wait for job list to load

                    scroll_job_list_panel(page, min_cards=25, delay=4)

                    try:
                        page.wait_for_selector(".job-card-container--clickable", timeout=5000)
                    except:
                        print("⚠️ Job cards not found after scroll, skipping page.")
                        start += 25
                        continue

                    job_cards = page.locator(".job-card-container--clickable")
                    total_cards = job_cards.count()

                    #print(f"📄 Found {total_cards} job cards on page offset {start}.")

                    if total_cards == 0:
                        print("🚫 No more job cards found. Ending scrape.")
                        break

                    for i in range(total_cards):
                        if max_count is not None and len(jobs) >= max_count:
                            print("✅ Reached max job count.")
                            return jobs

                        if i in seen_indices:
                            continue

                        seen_indices.add(i)

                        try:
                            print(f"🔄 Clicking on job card #{i + 1}")
                            job_cards.nth(i).click()
                            page.wait_for_timeout(3000)

                            job = extract_job_details(page, keyword)

                            # 🔍 Match against FILTER_KEYWORDS list
                            job_title_text = job.get("Job Title", "").lower()
                            if keyword.lower() in job_title_text:
                                jobs.append(job)
                                print(f"✅ [{global_job_index}] {job['Job Title']} at {job['Company Name']}")
                                global_job_index += 1
                                progress.update(1)
                            else:
                                print(f"⏭️ Skipped: '{job['Job Title']}' — does not match keyword: '{keyword}'")

                        except Exception as e:
                            print(f"❌ Error processing job card #{i + 1} on page offset {start}: {e}")

                    start += 25  # move to next page

        finally:
            context.close()
            browser.close()

    return jobs

def save_jobs(jobs, filename=None):
    """Save jobs to CSV, overwriting any existing file"""
    if not jobs:
        print("⚠️ No jobs to save.")
        return filename
    
    if filename is None:
        date_str = datetime.now().strftime("%d-%m-%Y")
        filename = f"linkedin_jobs_{date_str}.csv"
    
    keys = jobs[0].keys()
    with open(filename, "w", newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(jobs)
    
    print(f"💾 Saved {len(jobs)} jobs to {filename}")
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
            print(f"⏱️ Auto-saved at {datetime.now().strftime('%H:%M:%S')}")
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
        print(f"✅ Renamed file to {new_filename} to indicate complete scraping")
        return new_filename
    except Exception as e:
        print(f"❌ Failed to rename file: {e}")
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
                print(f"\n🔎 Scraping for keyword '{keyword}' in '{location}'")
                jobs = scrape_jobs(keyword, location, max_count=job_count)
                all_jobs.extend(jobs)
                
                # Periodically save after each keyword-location combination
                filename = save_jobs_periodic(all_jobs, save_interval)
        
        # Final save at the end
        filename = save_jobs(all_jobs, filename)
        
        # Rename the file to indicate complete scraping
        rename_file_complete(filename)
        print("✅ Scraping completed successfully!")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Stopping the bot due to keyboard interrupt (Ctrl+C)...")
        if all_jobs:
            save_jobs(all_jobs, filename)
            print("✅ CSV saved successfully (without COMPLETE marker)!")
        else:
            print("ℹ️ No jobs were collected to save.")
    except Exception as e:
        print(f"\n❌ An error occurred: {e}")
        if all_jobs:
            save_jobs(all_jobs, filename)
            print("✅ CSV saved successfully despite error (without COMPLETE marker)!")