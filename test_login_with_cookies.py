# test_login_with_cookies.py
from playwright.async_api import async_playwright

async def test_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Headed mode to see UI
        context = await browser.new_context(storage_state="storage_state.json")
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/feed", timeout=60000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path="login_test.png", full_page=True)
        print("📸 Screenshot saved as 'login_test.png'")
        await browser.close()

import asyncio
asyncio.run(test_login())
