import time
import pandas as pd
import google.generativeai as genai
import json
import re
import html
from datetime import datetime, timedelta
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

# LinkedIn Credentials (DO NOT hardcode credentials; use environment variables or manual login)
LINKEDIN_EMAIL = "you@example.com"
LINKEDIN_PASSWORD = "23ranjan"

# Configure Gemini AI
genai.configure(api_key="REDACTED_USE_ENV_GEMINI_API_KEY")

def ask_gemini(prompt):
    """Asks Gemini AI to extract job details from HTML."""
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini AI Error: {e}")
        return "AI Extraction Failed"

def scroll_down(page, times=3, delay=3):
    """Scroll down to load more jobs."""
    for _ in range(times):
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        time.sleep(delay)

def simplify_url(job_url):
    """Simplifies job URL by removing unnecessary parameters."""
    if job_url:
        base_url = job_url.split('?')[0]
        return base_url
    return job_url

def convert_to_date(posted_text):
    """Converts 'X days ago' into actual date (YYYY-MM-DD)"""
    today = datetime.today()

    if "just now" in posted_text.lower() or "hour" in posted_text.lower():
        return today.strftime("%Y-%m-%d")
    elif "day" in posted_text.lower():
        days_ago = int(re.search(r"\d+", posted_text).group()) if re.search(r"\d+", posted_text) else 0
        return (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    elif "week" in posted_text.lower():
        weeks_ago = int(re.search(r"\d+", posted_text).group()) if re.search(r"\d+", posted_text) else 0
        return (today - timedelta(weeks=weeks_ago)).strftime("%Y-%m-%d")
    elif "month" in posted_text.lower():
        months_ago = int(re.search(r"\d+", posted_text).group()) if re.search(r"\d+", posted_text) else 0
        new_month = today.month - months_ago
        new_year = today.year
        if new_month <= 0:
            new_month += 12
            new_year -= 1
        return datetime(new_year, new_month, today.day).strftime("%Y-%m-%d")
    return "Unknown"

def parse_ai_response(response):
    """Parses AI response and extracts job details."""
    response = response.strip()
    if response.startswith("```json"):
        response = response.replace("```json", "").strip()
    if response.endswith("```"):
        response = response[:-3].strip()

    print(f"🔹 Cleaned AI Response: {response}")

    try:
        data = json.loads(response)
        job_title = data.get("job_title", "Unknown")
        company_name = data.get("company_name", "Unknown")
        job_url = data.get("job_url", "Unknown")
        job_posted_text = data.get("job_posted", "Unknown")
        job_type = data.get("job_type", "Unknown")
        job_posted_date = convert_to_date(job_posted_text)
    
    except json.JSONDecodeError as e:
        print(f"❌ JSON Decode Error: {e}. Trying regex extraction...")
        job_title_match = re.search(r'"job_title":\s*"([^"]+)"', response)
        company_name_match = re.search(r'"company_name":\s*"([^"]+)"', response)
        job_url_match = re.search(r'"job_url":\s*"([^"]+)"', response)
        job_posted_match = re.search(r'"job_posted":\s*"([^"]+)"', response)
        job_type_match = re.search(r'"job_type":\s*"([^"]+)"', response)
        
        job_title = job_title_match.group(1) if job_title_match else "Unknown"
        company_name = company_name_match.group(1) if company_name_match else "Unknown"
        job_url = job_url_match.group(1) if job_url_match else "Unknown"
        job_posted_text = job_posted_match.group(1) if job_posted_match else "Unknown"
        job_type = job_type_match.group(1) if job_type_match else "Not Specified"
        job_posted_date = convert_to_date(job_posted_text)
    
    job_url = html.unescape(job_url)
    job_url = simplify_url(job_url)

    if job_url and job_url.startswith("/"):
        job_url = "https://www.linkedin.com" + job_url

    print(f"✅ Extracted Data: {job_title}, {company_name}, {job_posted_date}, {job_type}, {job_url}")
    return job_title, company_name, job_posted_date, job_type, job_url

def get_time_filter():
    while True:
        try:
            days = int(input("Enter the number of past days for job postings (e.g., 1, 3, 7, 30): ").strip())
            if days > 0:
                return f"r{days * 86400}"
            else:
                print("❌ Please enter a valid positive number.")
        except ValueError:
            print("❌ Invalid input. Please enter a valid number.")

def get_job_count():
    while True:
        try:
            count = int(input("Enter the number of jobs to extract: ").strip())
            if count > 0:
                return count
            else:
                print("❌ Please enter a valid positive number.")
        except ValueError:
            print("❌ Invalid input. Please enter a valid number.")

def scrape_linkedin_jobs(job_title, job_location, time_filter):
    scraped_jobs = []
    scraped_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()

        print("🔄 Checking LinkedIn session...")
        page.goto("https://www.linkedin.com/feed/", timeout=60000)
        time.sleep(5)
        
        if "login" in page.url:
            print("❌ You are not logged in! Please log in manually before running the script.")
            browser.close()
            return []

        print("✅ Successfully connected to logged-in session!")

        print(f"🔄 Searching for jobs: {job_title} in {job_location}...")
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={job_title}&location={job_location}&f_TPR={time_filter}"
        page.goto(search_url, timeout=60000)
        time.sleep(5)

        while len(scraped_jobs) < job_count:
            scroll_down(page, times=5, delay=2)
            job_cards = page.locator(".job-card-container").all()

            if not job_cards:
                print("❌ No jobs found or LinkedIn blocked the bot.")
                break

            for job_card in job_cards:
                if len(scraped_jobs) >= job_count:
                    break

                try:
                    job_url = simplify_url(job_card.locator("a").get_attribute("href"))
                    if job_url in scraped_urls:
                        continue

                    job_title = job_card.locator(".job-card-list__title").text_content(timeout=5000).strip()
                    company_name = job_card.locator(".job-card-container__company-name").text_content(timeout=5000).strip()
                    job_posted_text = job_card.locator(".job-card-container__footer-item").text_content(timeout=5000).strip()
                    # Find all elements matching the job type selector
                    job_type_elements = job_card.locator("span.ui-label.ui-label--accent-3.text-body-small").all()

                    # Debugging Step 1: Check if elements exist
                    if not job_type_elements:
                         print("⚠️ No job type elements found!")
                    else:
                         print(f"✅ Found {len(job_type_elements)} job type elements")

                    # Extract text only if elements exist
                    if job_type_elements:
                        job_type_list = [el.text_content().strip().split("\n")[0] for el in job_type_elements]

                        # Debugging Step 2: Print raw extracted values
                        print(f"📝 Raw Job Type Values: {job_type_list}") 

                        # Extract only valid job types
                        valid_job_types = ["Full-time", "Part-time", "Internship", "Contract", "Temporary", "Volunteer", "Remote"]
                        job_type = next((text for text in job_type_list if text in valid_job_types), "Not Found")

                        print(f"🎯 Extracted Job Type: {job_type}")  # Debugging Output
                    else:
                        job_type = "Not Found"


                    job_posted_date = convert_to_date(job_posted_text)

                except Exception as e:
                    print(f"❌ Error extracting job: {e}")
                    job_title, company_name, job_posted_date, job_type, job_url = None, None, None, None, None

                if not job_title or not company_name or not job_posted_date or not job_type or not job_url:
                    try:
                        html_content = job_card.inner_html()
                        ai_prompt = f"Extract job title, company name, job type (Full-time, Part-time, Contract, Internship), job URL, and job posted date from this HTML:\n{html_content}"
                        ai_response = ask_gemini(ai_prompt)
                        job_title, company_name, job_posted_date, job_type, job_url = parse_ai_response(ai_response)
                    except Exception as error:
                        print(f"❌ AI Parsing Error: {error}")
                        job_title, company_name, job_posted_date, job_type, job_url = "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"
                
                scraped_urls.add(job_url)
                scraped_jobs.append({"Job Title": job_title, "Company Name": company_name, "Job Posted Date": job_posted_date, "Job Type": job_type, "Job URL": job_url})

                print(f"✅ Scraped Job {len(scraped_jobs)}/50: {job_title}, {company_name}, {job_posted_date}")
              # print(html_content)  # Check if job type is actually present
                print(f"Extracted Job Type: {job_type}")
 
                time.sleep(2)

        browser.close()
        return scraped_jobs

job_title = input("Enter the job title: ")
job_location = input("Enter the job location: ")
time_filter = get_time_filter()
job_count = get_job_count()

scraped_jobs = scrape_linkedin_jobs(job_title, job_location, time_filter)

if scraped_jobs:
    pd.DataFrame(scraped_jobs,).to_csv("linkedin_jobs.csv", index=False)
    print("✅ CSV saved successfully!")
else:
    print("❌ No data extracted.")
