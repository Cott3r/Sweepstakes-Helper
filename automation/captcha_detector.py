"""Detection engine for CAPTCHAs, bot challenges, and Cloudflare Turnstile."""

from typing import List, Optional, Tuple
from playwright.async_api import Page

from utils.logger import logger


class CaptchaDetector:
    """Scans Playwright pages and iframes to detect unsolvable CAPTCHAs and bot challenges."""

    CAPTCHA_SELECTORS = {
        "Cloudflare Turnstile": [
            "iframe[src*='challenges.cloudflare.com']",
            "iframe[src*='turnstile']",
            "div.cf-turnstile",
            "#cf-turnstile",
            ".cf-challenge",
            "#challenge-running",
            "#cf-wrapper",
        ],
        "Google reCAPTCHA": [
            "iframe[src*='google.com/recaptcha']",
            "iframe[src*='recaptcha']",
            "iframe[title*='reCAPTCHA']",
            "div.g-recaptcha",
            "#g-recaptcha",
            "#g-recaptcha-response",
            ".recaptcha-checkbox",
        ],
        "hCaptcha": [
            "iframe[src*='hcaptcha.com']",
            "div.h-captcha",
            "#h-captcha",
            "iframe[data-hcaptcha-widget-id]",
        ],
        "Arkose FunCaptcha": [
            "iframe[src*='arkoselabs']",
            "iframe[src*='funcaptcha']",
            "#fc-iframe-wrap",
        ],
        "AWS WAF Captcha": [
            "#aws-waf-captcha",
            "iframe[src*='awswaf']",
        ],
        "Fastly Challenge / CAPTCHA": [
            "iframe#fastly-captcha-iframe",
            "iframe[src*='captcha-iframe.html']",
            "iframe[src*='_fs-ch-']",
            ".fastly-challenge",
            "[data-testid='fastly-captcha-backdrop']",
            "[data-challenge-status='captcha_prompted']",
        ],
        "Generic Text/Image Captcha": [
            "img[src*='captcha']",
            "img[id*='captcha']",
            "input[name*='captcha']",
            "input[id*='captcha']",
            "input[placeholder*='captcha' i]",
            "input[placeholder*='sicherheitscode' i]",
        ],
    }

    @classmethod
    async def detect_captcha(cls, page: Page) -> Tuple[bool, Optional[str]]:
        """Scans the page and attached frames for active CAPTCHA elements.

        Returns:
            Tuple of (has_captcha: bool, captcha_name: Optional[str])
        """
        for captcha_type, selectors in cls.CAPTCHA_SELECTORS.items():
            for selector in selectors:
                try:
                    locator = page.locator(selector)
                    count = await locator.count()
                    if count > 0:
                        # Ensure element is actually attached/visible or waiting for interaction
                        is_visible = False
                        for i in range(count):
                            el = locator.nth(i)
                            if await el.is_visible():
                                is_visible = True
                                break

                        # For Cloudflare challenge wrappers or iframes, visibility is not strictly required
                        if is_visible or "turnstile" in selector or "challenge" in selector:
                            logger.warning(f"Detected {captcha_type} with selector: '{selector}'")
                            return True, captcha_type
                except Exception:
                    # Ignore transient selector evaluation errors
                    continue

        # Also inspect frames for captcha URLs
        for frame in page.frames:
            url = frame.url.lower()
            if "challenges.cloudflare.com" in url or "turnstile" in url:
                return True, "Cloudflare Turnstile (Frame)"
            if "recaptcha" in url or "google.com/recaptcha" in url:
                return True, "Google reCAPTCHA (Frame)"
            if "hcaptcha.com" in url:
                return True, "hCaptcha (Frame)"
            if "arkoselabs" in url:
                return True, "Arkose FunCaptcha (Frame)"
            if "captcha-iframe.html" in url or "_fs-ch-" in url or "fastly-captcha" in url:
                return True, "Fastly CAPTCHA (Frame)"

        return False, None
