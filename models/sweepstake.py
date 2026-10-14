"""Data models for sweepstakes, user profiles, and execution status."""

from datetime import datetime, date, timezone
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, HttpUrl, field_validator


class SweepstakeStatus(str, Enum):
    """Execution status of a sweepstake in the pipeline."""
    PENDING = "PENDING"
    SKIPPED = "SKIPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class UserProfile(BaseModel):
    """Personal profile configuration used to fill out sweepstake entry forms."""
    first_name: str = Field(..., description="First name / Vorname")
    last_name: str = Field(..., description="Last name / Nachname")
    email: str = Field(..., description="Primary email address")
    phone: str = Field(..., description="Phone number with country code (e.g. +43...)")
    street: str = Field(..., description="Street name / Straße")
    house_number: str = Field(..., description="House / Apartment number / Hausnummer")
    postal_code: str = Field(..., description="Austrian postal code / Postleitzahl (4 digits)")
    city: str = Field(..., description="City or town / Ort")
    country: str = Field(default="Österreich", description="Country name in German or English")
    birth_date: Optional[str] = Field(None, description="Date of birth in DD.MM.YYYY or YYYY-MM-DD")
    salutation: str = Field(default="Herr", description="Anrede: Herr / Frau / Diverse")

    @property
    def full_name(self) -> str:
        """Returns the full name formatted as First Last."""
        return f"{self.first_name} {self.last_name}"

    @property
    def address_line(self) -> str:
        """Returns the street address line."""
        return f"{self.street} {self.house_number}".strip()


class SweepstakeItem(BaseModel):
    """Represents a discovered sweepstake contest."""
    id: Optional[int] = Field(None, description="Internal database ID")
    source: str = Field(..., description="Source name (e.g., gewinnspielsammlung_at, r_sweepstakes)")
    external_id: str = Field(..., description="Unique identifier from the source")
    url: str = Field(..., description="Destination entry URL")
    title: str = Field(..., description="Title or description of the prize/contest")
    deadline: Optional[str] = Field(None, description="Expiration/closing date")
    eligibility_country: str = Field(default="AT", description="Target eligibility (AT, EU, WW)")
    requires_manual_entry: bool = Field(default=False, description="Flag indicating CAPTCHA, quiz or complex workflow")
    status: SweepstakeStatus = Field(default=SweepstakeStatus.PENDING, description="Current processing status")
    is_daily_recurring: bool = Field(default=False, description="True if contest can be entered once every calendar day")
    quiz_answer: Optional[str] = Field(None, description="Answer to the quiz question if provided by source")
    error_log: Optional[str] = Field(None, description="Error message if entry failed")
    last_attempted_at: Optional[datetime] = Field(None, description="Timestamp of last submission attempt")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Record creation timestamp")
    completed_at: Optional[datetime] = Field(None, description="Timestamp when successfully entered")

    model_config = {
        "from_attributes": True
    }
