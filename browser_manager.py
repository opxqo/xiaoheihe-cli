"""
浏览器管理模块：Cookie 持久化、验证、登录流程
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from pathlib import Path
from typing import Optional, List

from playwright.async_api import async_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.xiaoheihe.cn"


class BrowserManager:
    """管理 Playwright 浏览器实例，负责 Cookie 的加载、验证和持久化"""

    COOKIES_FILE = "cookies.json"

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._api_page = None
        self._cookies: List[dict] = []
        self._heybox_id: Optional[str] = None

    @property
    def cookies(self) -> List[dict]:
        return self._cookies

    @property
    def heybox_id(self) -> Optional[str]:
        return self._heybox_id

    @property
    def api_page(self):
        return self._api_page

    # ==================== 初始化 ====================

    async def init(self):
        """初始化：尝试加载已保存的 Cookie，验证有效性"""
        saved_cookies = self._load_cookies()

        if saved_cookies:
            logger.info("已加载保存的 Cookie")
            self._cookies = saved_cookies
            self._heybox_id = self._extract_heybox_id(saved_cookies)

            if await self._verify_cookies():
                logger.info("Cookie 有效，直接使用 API 模式")
                await self._ensure_api_page()
                return

            logger.warning("Cookie 已过期，需要重新登录")
            self._cookies = []
            self._heybox_id = None

        # 无有效 Cookie，打开浏览器让用户登录
        await self.open_browser_for_login()

    async def inject_cookies(self, cookie_string: str):
        """
        从浏览器 Cookie 字符串直接注入（跳过登录/验证码流程）。
        格式: "key1=val1; key2=val2; ..."
        """
        cookies = self._parse_cookie_string(cookie_string)
        if not cookies:
            raise ValueError("无法解析 Cookie 字符串")

        logger.info("已从字符串解析 %d 个 Cookie", len(cookies))
        self._cookies = cookies
        self._heybox_id = self._extract_heybox_id(cookies)

        if self._heybox_id:
            logger.info("用户 ID: %s", self._heybox_id)

        self._save_cookies(cookies)
        logger.info("Cookie 已保存到 %s", self.COOKIES_FILE)

    async def start_session(self):
        """
        启动会话：单次导航到首页后常驻。
        之后所有 API 调用用 fetch() 模式，不再触发页面导航 → 不再弹验证码。
        """
        await self._ensure_api_page()

        try:
            await self._api_page.goto(
                f"{_BASE_URL}/app/bbs/home",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            logger.info("会话就绪 (单页模式, heybox_id=%s)", self._heybox_id)
        except Exception as e:
            logger.warning("首页导航异常 (可能需要过验证码): %s", e)
            logger.info("会话已就绪，API 调用将在当前页面内进行")

    @staticmethod
    def _parse_cookie_string(cookie_string: str) -> List[dict]:
        """
        将浏览器 Cookie 字符串解析为 Playwright 格式的 cookie 列表。

        处理:
        - 自动 URL 解码值（浏览器复制出的 Cookie 通常包含 %XX 编码）
        - 添加 SameSite=Lax / Secure 属性确保跨域请求携带 Cookie
        """
        from urllib.parse import unquote

        cookies = []
        for pair in cookie_string.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            value = unquote(value.strip())
            if not name:
                continue
            cookies.append({
                "name": name,
                "value": value,
                "domain": ".xiaoheihe.cn",
                "path": "/",
                "sameSite": "Lax",
                "secure": False,  # 小黑盒支持 http，不需要 strict secure
            })
        return cookies

    async def _verify_cookies(self) -> bool:
        """验证 Cookie 是否有效"""
        if not self._cookies:
            return False

        if not self._context:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                channel="msedge", headless=True,
            )
            self._context = await self._browser.new_context()
            await self._context.add_cookies(self._cookies)

        captured = [None]

        async def on_response(response):
            if "/bbs/app/link/tree" in response.url:
                try:
                    body = await response.text()
                    captured[0] = json.loads(body)
                except Exception:
                    pass

        try:
            test_page = await self._context.new_page()
            listener = test_page.on("response", on_response)
            try:
                await test_page.goto(
                    "https://www.xiaoheihe.cn/app/bbs/link/179245676",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await asyncio.sleep(3)
            finally:
                # 保证监听器移除
                try:
                    test_page.remove_listener("response", on_response)
                except Exception:
                    pass
                await test_page.close()

            data = captured[0]
            return data is not None and data.get("status") == "ok"

        except Exception as e:
            logger.warning("Cookie 验证失败: %s", e)
            return False

    async def _ensure_api_page(self):
        """确保存在用于 API 请求的页面"""
        if self._api_page and not self._api_page.is_closed():
            return

        if not self._context:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                channel="msedge", headless=True,
            )
            self._context = await self._browser.new_context()
            if self._cookies:
                await self._context.add_cookies(self._cookies)

        self._api_page = await self._context.new_page()

    # ==================== 登录流程 ====================

    async def open_browser_for_login(self):
        """
        打开浏览器窗口等待用户手动登录。
        轮询检测 Cookie 是否生效，最长等待 max_wait 秒。
        """
        print("\n" + "=" * 60)
        print("需要登录验证")
        print("正在打开浏览器窗口...")
        print("请在浏览器中通过验证码或登录")
        print("系统将自动检测登录状态，无需手动操作")
        print("=" * 60 + "\n")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless, channel="msedge",
        )
        self._context = await self._browser.new_context()

        page = await self._context.new_page()
        await page.goto(
            "https://www.xiaoheihe.cn/app/bbs/home",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        logger.info("浏览器已打开，等待用户完成验证码登录...")

        check_page = await self._context.new_page()

        max_wait = 300
        check_interval = 3
        elapsed = 0

        while elapsed < max_wait:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            captured = [None]

            async def on_check(response):
                if "/bbs/app/link/tree" in response.url:
                    try:
                        body = await response.text()
                        captured[0] = json.loads(body)
                    except Exception:
                        pass

            listener = check_page.on("response", on_check)
            try:
                await check_page.goto(
                    "https://www.xiaoheihe.cn/app/bbs/link/179245676",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await asyncio.sleep(3)

                status = (captured[0] or {}).get("status", "")
                if status == "ok" and "result" in (captured[0] or {}):
                    logger.info("检测到有效 Cookie！登录成功")
                    self._cookies = await self._context.cookies()
                    self._heybox_id = self._extract_heybox_id(self._cookies)
                    self._save_cookies(self._cookies)
                    logger.info(f"Cookie 已保存到 {self.COOKIES_FILE}")
                    if self._heybox_id:
                        logger.info(f"用户 ID: {self._heybox_id}")
                    await page.close()
                    await self._ensure_api_page()
                    return
                elif elapsed % 15 == 0:
                    logger.info("... 等待中 (%ds / %ds)", elapsed, max_wait)

            except Exception:
                if elapsed % 15 == 0:
                    logger.info("... 等待中 (%ds / %ds)", elapsed, max_wait)
            finally:
                # 每次循环必须移除监听器，防止泄漏
                try:
                    check_page.remove_listener("response", on_check)
                except Exception:
                    pass

        # 超时
        logger.warning("超时 (%ds)，未检测到有效 Cookie", max_wait)
        await check_page.close()
        await page.close()

    # ==================== Cookie 刷新 ====================

    async def refresh_cookies(self):
        """关闭当前会话并重新进入登录流程"""
        await self._shutdown()
        await self.open_browser_for_login()

    # ==================== 关闭 ====================

    async def close(self):
        """关闭浏览器"""
        if self._api_page:
            try:
                await self._api_page.close()
            except Exception:
                pass
        await self._shutdown()

    async def _shutdown(self):
        """统一释放所有资源（供 close / refresh_cookies 共用）"""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

        self._context = None
        self._browser = None
        self._playwright = None
        self._api_page = None

    # ==================== Cookie 持久化 ====================

    def _load_cookies(self) -> List[dict]:
        cookies_path = Path(self.COOKIES_FILE)
        if cookies_path.exists():
            try:
                with open(cookies_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("加载 Cookie 失败: %s", e)
        return []

    def _save_cookies(self, cookies: List[dict]):
        cookies_path = Path(self.COOKIES_FILE)
        with open(cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _extract_heybox_id(cookies: List[dict]) -> Optional[str]:
        for cookie in cookies:
            if cookie.get("name") in ("heybox_id", "user_heybox_id"):
                return cookie.get("value")
        return None
