"""Playwright stealth browser session manager with anti-detection evasions."""

import asyncio
from typing import AsyncGenerator, Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from config.settings import settings
from utils.logger import logger

# Try importing playwright-stealth
try:
    from playwright_stealth import stealth_async
    HAS_PLAYWRIGHT_STEALTH = True
except ImportError:
    HAS_PLAYWRIGHT_STEALTH = False


# JavaScript snippet injected before any document loads to eliminate bot fingerprints
STEALTH_INJECTION_JS = """
(() => {
    // 1. Overwrite navigator.webdriver to false/undefined
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
    });

    // 2. Mock Chrome runtime
    window.chrome = {
        app: {
            isInstalled: false,
            InstallState: { DISABLED: 'DISABLED', INSTALLED: 'INSTALLED', NOT_INSTALLED: 'NOT_INSTALLED' },
            RunningState: { CANNOT_RUN: 'CANNOT_RUN', READY_TO_RUN: 'READY_TO_RUN', RUNNING: 'RUNNING' }
        },
        runtime: {
            OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
            OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
            PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' }
        }
    };

    // 3. Mock navigator.languages for Austria (de-AT)
    Object.defineProperty(navigator, 'languages', {
        get: () => ['de-AT', 'de', 'en-US', 'en'],
    });

    // 4. Mock navigator.plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });

    // 5. Mock WebGL vendor & renderer
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) {
            return 'Google Inc. (Intel)';
        }
        if (parameter === 37446) {
            return 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)';
        }
        return getParameter.apply(this, arguments);
    };

    // 6. Notification permission
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
    );
})();
"""


class BrowserManager:
    """Manages stealth Playwright browser contexts for headless and headful runs."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self) -> None:
        """Launches Playwright and Chromium with anti-bot command line arguments."""
        self._playwright = await async_playwright().start()

        chromium_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--disable-gpu",
            "--window-size=1366,768",
        ]

        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=chromium_args,
        )
        mode = "Headless" if self.headless else "Interactive (Headful)"
        logger.debug(f"Playwright Chromium started in {mode} mode.")

    async def create_stealth_context(self) -> BrowserContext:
        """Creates a browser context configured with Austrian locale, timezone, and viewport."""
        if not self._browser:
            raise RuntimeError("Browser not started. Call start() first.")

        context = await self._browser.new_context(
            user_agent=settings.DEFAULT_USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="de-AT",
            timezone_id="Europe/Vienna",
            color_scheme="light",
            has_touch=False,
            is_mobile=False,
            device_scale_factor=1,
            permissions=["geolocation"],
            geolocation={"latitude": 48.2082, "longitude": 16.3738},  # Vienna coordinates
        )

        # Inject stealth scripts before any page load
        await context.add_init_script(STEALTH_INJECTION_JS)
        return context

    async def create_stealth_page(self, context: Optional[BrowserContext] = None) -> Page:
        """Creates a new page with stealth enhancements."""
        ctx = context or await self.create_stealth_context()
        page = await ctx.new_page()

        if HAS_PLAYWRIGHT_STEALTH:
            try:
                await stealth_async(page)
            except Exception as e:
                logger.debug(f"playwright_stealth helper note: {e}")

        # Set default timeouts
        page.set_default_timeout(settings.ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(settings.BROWSER_TIMEOUT_MS)
        return page

    async def close(self) -> None:
        """Closes browser and Playwright runtime cleanly."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.debug("BrowserManager closed.")
