# save_linkedin_cookies.py
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Must be non-headless
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login")
        print("➡️ Please log in manually...")

        # Wait until you're on the home/feed page (you can also adjust this selector)
        await page.wait_for_url("**/feed*", timeout=120_000)
        print("✅ Login successful. Saving cookies...")
        await context.storage_state(path="storage_state.json")
        await browser.close()

asyncio.run(main())
