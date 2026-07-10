"""
Blinkit "Hot Wheels" stock watcher -> Telegram notifier
(single-run version, meant to be triggered on a schedule by GitHub Actions)

Each run: opens Blinkit, sets the pincode, searches, reads results, compares
against the last known state (stored in state.json, committed back to the
repo by the workflow), and messages Telegram only if something changed.

Setup: see README.md
"""

import asyncio
import json
import os
import sys
import time
import requests
from playwright.async_api import async_playwright

# ---------------- CONFIG ----------------
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PINCODE = os.environ.get("BLINKIT_PINCODE", "110001")
SEARCH_TERM = os.environ.get("SEARCH_TERM", "hot wheels")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"
# -----------------------------------------


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[!] Failed to send Telegram message: {e}")


def load_last_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_last_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


async def set_location(page, pincode: str) -> None:
    await page.goto("https://blinkit.com", timeout=30000)
    await page.wait_for_timeout(2000)

    try:
        loc_button = page.get_by_text("select location", exact=False).first
        if await loc_button.count() > 0:
            await loc_button.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    try:
        loc_input = page.locator("input").first
        await loc_input.click()
        await loc_input.fill(pincode)
        await page.wait_for_timeout(1500)

        suggestion = page.locator(
            "[class*='address'], [class*='locality'], [class*='LocationSearchList']"
        ).first
        if await suggestion.count() > 0:
            await suggestion.click()
            await page.wait_for_timeout(1500)
    except Exception as e:
        print(f"[!] Could not set location automatically: {e}")


async def search_and_read(page, query: str) -> list[dict]:
    try:
        search_box = page.locator("input[type='text'], input[type='search']").first
        await search_box.click()
        await search_box.fill(query)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2500)
    except Exception as e:
        print(f"[!] Search failed: {e}")
        return []

    products = []
    cards = page.locator("[data-pf='reset'], [class*='ProductCard'], [class*='product-card']")
    count = await cards.count()

    for i in range(count):
        card = cards.nth(i)
        try:
            text_content = (await card.inner_text()).strip()
        except Exception:
            continue
        if not text_content:
            continue

        lines = [l for l in text_content.split("\n") if l.strip()]
        name = lines[0] if lines else "Unknown product"
        lowered = text_content.lower()
        in_stock = "out of stock" not in lowered and "notify me" not in lowered

        products.append({"name": name, "in_stock": in_stock})

    return products


async def run_check() -> list[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        page = await browser.new_page()
        await set_location(page, PINCODE)
        products = await search_and_read(page, SEARCH_TERM)
        await browser.close()
        return products


def main():
    last_state = load_last_state()

    try:
        products = asyncio.run(run_check())
    except Exception as e:
        print(f"[!] Error during check: {e}")
        sys.exit(1)

    in_stock = sorted(p["name"] for p in products if p["in_stock"])
    previous = sorted(last_state.get("in_stock", []))

    if in_stock and in_stock != previous:
        msg = (f"🎉 <b>Hot Wheels in stock</b> near {PINCODE}!\n\n"
               + "\n".join(f"• {n}" for n in in_stock))
        send_telegram_message(msg)
        print(f"[{time.strftime('%H:%M:%S')}] Notified: {in_stock}")
    elif not in_stock and previous:
        send_telegram_message(f"❌ Hot Wheels no longer in stock near {PINCODE}.")
        print(f"[{time.strftime('%H:%M:%S')}] Went out of stock.")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] No change. In stock: {len(in_stock)} item(s).")

    last_state["in_stock"] = in_stock
    save_last_state(last_state)


if __name__ == "__main__":
    main()
