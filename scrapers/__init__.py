"""Scrapers package."""

from scrapers.base import BaseScraper
from scrapers.gewinnspielsammlung import GewinnspielsammlungScraper
from scrapers.reddit import RedditSweepstakesScraper

__all__ = ["BaseScraper", "GewinnspielsammlungScraper", "RedditSweepstakesScraper"]
