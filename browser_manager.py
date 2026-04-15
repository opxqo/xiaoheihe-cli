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

    async def login_with_phone(self, phone: str, code_callback=None):
        """
        手机号 + 验证码自动登录流程。

        Args:
            phone: 完整手机号（含区号），如 "+8613800138000" 或 "+49123456789"
            code_callback: 异步回调，返回用户输入的验证码字符串。
                           为 None 时使用默认的 input() 等待（适合 headless 服务器场景）。

        Returns:
            True 登录成功, False 失败
        """
        print("\n" + "=" * 60)
        print("📱 小黑盒手机号登录")
        print(f"   号码: {phone}")
        print("=" * 60)

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

        # ===== Step 1: 找到并点击登录入口 =====
        try:
            # 小黑盒登录按钮可能在多个位置
            login_btn_selectors = [
                'text="登录"',
                'text="登 录"',
                '[class*="login"]',
                '[class*="Login"]',
                'a[href*="login"]',
                '[data-testid="login-btn"]',
            ]
            clicked = False
            for sel in login_btn_selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=3000)
                    if el:
                        await el.click(timeout=3000)
                        clicked = True
                        logger.info("点击了登录按钮: %s", sel)
                        break
                except Exception:
                    continue

            if not clicked:
                # 尝试直接导航到登录页
                await page.goto(
                    "https://www.xiaoheihe.cn/login",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )

            await asyncio.sleep(2)  # 等待弹窗/页面加载

        except Exception as e:
            logger.warning("查找登录按钮异常，尝试直接导航: %s", e)
            await page.goto(
                "https://www.xiaoheihe.cn/login",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(2)

        # ===== Step 2: 输入手机号 =====
        phone_input_selectors = [
            'input[type="tel"]',
            'input[placeholder*="手机"]',
            'input[placeholder*="电话"]',
            'input[name="phone"]',
            'input[name="mobile"]',
            'input[inputmode="tel"]',
            'input[type="text"]:nth-of-type(1)',  # 备用：第一个文本框
        ]
        phone_entered = False
        for sel in phone_input_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el:
                    await el.fill(phone, timeout=3000)
                    phone_entered = True
                    logger.info("已输入手机号到: %s", sel)
                    break
            except Exception:
                continue

        if not phone_entered:
            print("\n⚠️ 未找到手机号输入框。请手动在浏览器中操作。")
            print("   完成登录后脚本会自动检测...")
            return await self._wait_for_login_result(page)

        await asyncio.sleep(0.5)

        # ===== Step 3: 点击发送验证码 =====
        send_btn_selectors = [
            'text="获取验证码"',
            'text="发送验证码"',
            'text="获取"',
            'text="发送"',
            'text="Send Code"',
            '[class*="send-code"]',
            '[class*="send_code"]',
            '[class*="sms"]',
            'button:not([type="submit"]):not(:has-text("登录")):not(:has-text("登 录"))',
        ]
        sent = False
        for sel in send_btn_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    sent = True
                    logger.info("点击了发送验证码按钮: %s", sel)
                    break
            except Exception:
                continue

        if not sent:
            # 最后尝试：找所有 button 里包含 "验证码"/"码" 的文字
            try:
                buttons = page.query_selector_all("button")
                for btn in buttons:
                    text = (await btn.inner_text()).strip()
                    if any(kw in text for kw in ("验证码", "码", "code", "Code")):
                        await btn.click(timeout=2000)
                        sent = True
                        logger.info("通过文本匹配点击了验证码按钮: %s", text)
                        break
            except Exception:
                pass

        if not sent:
            print("\n⚠️ 未找到'发送验证码'按钮。请手动在浏览器中完成登录流程。")
            return await self._wait_for_login_result(page)

        print(f"\n✉️ 验证码已发送至 {phone}")
        print("-" * 40)

        # ===== Step 4: 获取验证码 =====
        if code_callback:
            code = await code_callback()
        else:
            code = input("请输入收到的验证码: ").strip()

        if not code or len(code) < 4:
            print("❌ 验证码无效")
            return False

        # ===== Step 5: 输入验证码并提交 =====
        code_input_selectors = [
            'input[type="number"]',
            'input[inputmode="numeric"]',
            'input[placeholder*="验证码"]',
            'input[placeholder*="code"]',
            'input[name="code"]',
            'input[name="sms_code"]',
            'input[type="text"]:nth-of-type(2)',
        ]
        code_entered = False
        for sel in code_input_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el:
                    await el.fill(code, timeout=3000)
                    code_entered = True
                    logger.info("已输入验证码到: %s", sel)
                    break
            except Exception:
                continue

        if not code_entered:
            print("⚠️ 未找到验证码输入框。请在浏览器中手动输入。")
            return await self._wait_for_login_result(page)

        await asyncio.sleep(0.5)

        # 点击登录/提交按钮
        submit_selectors = [
            'button:has-text("登录")',
            'button:has-text("登 录")',
            'button:has-text("Login")',
            'button[type="submit"]',
            '[class*="submit"]',
        ]
        for sel in submit_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    logger.info("点击了提交/登录按钮: %s", sel)
                    break
            except Exception:
                continue

        # ===== Step 6: 检测登录结果 =====
        print("⏳ 正在验证登录状态...")

        for i in range(10):
            await asyncio.sleep(3)
            cookies = await self._context.cookies()
            heybox_id = self._extract_heybox_id(cookies)
            if heybox_id:
                print(f"\n{'=' * 60}")
                print(f"  ✅ 登录成功!")
                print(f"  用户 ID: {heybox_id}")
                print(f"{'=' * 60}\n")

                self._cookies = cookies
                self._heybox_id = heybox_id
                self._save_cookies(cookies)
                logger.info("Cookie 已保存")

                # 启动 API 会话
                await page.close()
                await self._ensure_api_page()
                return True

        print("\n❌ 登录超时或失败（10次检测均未发现有效 Cookie）")
        return False

    async def _wait_for_login_result(self, page):
        """回退方案：等待用户手动完成登录后自动检测"""
        print("\n请在浏览器中完成登录操作...")
        max_wait = 180  # 3 分钟
        for i in range(max_wait // 5):
            await asyncio.sleep(5)
            cookies = await self._context.cookies()
            heybox_id = self._extract_heybox_id(cookies)
            if heybox_id:
                print(f"\n✅ 登录成功! 用户 ID: {heybox_id}")
                self._cookies = cookies
                self._heybox_id = heybox_id
                self._save_cookies(cookies)
                await page.close()
                await self._ensure_api_page()
                return True
        print("\n❌ 等待超时")
        return False

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
