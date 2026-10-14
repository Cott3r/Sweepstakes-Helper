"""Automation package."""

from automation.browser import BrowserManager
from automation.captcha_detector import CaptchaDetector
from automation.form_filler import FormFiller
from automation.runner import AutomationRunner

__all__ = ["BrowserManager", "CaptchaDetector", "FormFiller", "AutomationRunner"]
