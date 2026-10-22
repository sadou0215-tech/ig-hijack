import asyncio, os
from playwright.async_api import async_playwright

PROFILE = "paypay_profile"

async def _open(p):
    ctx = await p.chromium.launch_persistent_context(
        PROFILE, headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    return ctx, page

async def status(link: str) -> str:
    """'unpaid' | 'pending'(=保留/受取可能) | 'received' を返す"""
    async with async_playwright() as p:
        ctx, page = await _open(p)
        try:
            await page.goto(link, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            body = await page.inner_text("body")

            if "受け取り済み" in body or "受け取りが完了" in body:
                return "received"
            if await page.get_by_role("button", name="受け取る").count() > 0:
                return "pending"          # 保留＝受け取り可能
            if "受け取り" in body and "期限" not in body:
                return "pending"
            return "unpaid"
        finally:
            await ctx.close()

async def receive(link: str) -> bool:
    """保留を実際に受け取る"""
    async with async_playwright() as p:
        ctx, page = await _open(p)
        try:
            await page.goto(link, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            for _ in range(3):  # 確認画面が2段のことがある
                btn = page.get_by_role("button", name="受け取る")
                if await btn.count() == 0:
                    break
                await btn.first.click()
                await page.wait_for_timeout(3500)
            return "受け取り" in await page.inner_text("body")
        finally:
            await ctx.close()
