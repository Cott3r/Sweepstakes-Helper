"""Semantic form filler capable of matching and filling Austrian and international sweepstake forms."""

import re
from typing import Dict, List, Optional, Tuple
from playwright.async_api import Page, Locator

from models.sweepstake import UserProfile
from utils.logger import logger


class FormFiller:
    """Intelligently detects form fields, fills profile details, manages checkboxes, and locates submit triggers."""

    def __init__(self, profile: UserProfile, quiz_answer: Optional[str] = None):
        self.profile = profile
        self.quiz_answer = quiz_answer

    async def fill_form(self, page: Page) -> Dict[str, str]:
        """Scans the page and fills all recognized inputs semantically.

        Returns:
            Dictionary mapping filled field names to filled values.
        """
        filled_fields: Dict[str, str] = {}

        # 1. Fill Text / Email / Tel / Number inputs
        inputs = await page.locator("input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='checkbox']):not([type='radio']), textarea").all()
        for field in inputs:
            try:
                if not await field.is_visible() or not await field.is_enabled():
                    continue

                attr_name = (await field.get_attribute("name") or "").lower()
                attr_id = (await field.get_attribute("id") or "").lower()
                tabindex = await field.get_attribute("tabindex")

                # Detect honeypots: negative tabindex, common honeypot names, or off-screen positioning
                if tabindex == "-1" or any(hp in attr_name or hp in attr_id for hp in ["hp_", "hpr", "honeypot"]):
                    logger.debug(f"Skipping potential honeypot field '{attr_name or attr_id}'")
                    continue

                box = await field.bounding_box()
                if not box or box["x"] < -100 or box["y"] < -100:
                    logger.debug(f"Skipping off-screen input '{attr_name or attr_id}'")
                    continue

                attr_placeholder = (await field.get_attribute("placeholder") or "").lower()
                attr_aria = (await field.get_attribute("aria-label") or "").lower()
                input_type = (await field.get_attribute("type") or "text").lower()

                # Get associated label text if possible
                label_text = await self._get_associated_label_text(page, field, attr_id)
                combined_descriptor = f"{attr_name} {attr_id} {attr_placeholder} {attr_aria} {label_text}".lower()

                # Match semantic categories
                val = self._match_field_value(combined_descriptor, input_type)
                if val is not None:
                    # Only fill if currently empty
                    current_val = await field.input_value()
                    if not current_val:
                        await field.click(timeout=3000)
                        await field.fill("")
                        await field.type(val, delay=35)  # Realistic typing delay
                        filled_fields[attr_name or attr_id or label_text[:20]] = val
                        logger.debug(f"Filled field '{attr_name or attr_id}' with '{val}'")
            except Exception as e:
                logger.debug(f"Error inspecting input field: {e}")

        # 2. Fill Select Dropdowns (Country, Salutation, Birthdate year/month/day, Quiz/Solution)
        selects = await page.locator("select").all()
        for sel in selects:
            try:
                if not await sel.is_visible() or not await sel.is_enabled():
                    continue

                attr_name = (await sel.get_attribute("name") or "").lower()
                attr_id = (await sel.get_attribute("id") or "").lower()
                label_text = await self._get_associated_label_text(page, sel, attr_id)
                combined_descriptor = f"{attr_name} {attr_id} {label_text}".lower()

                # Check Country dropdown
                if any(k in combined_descriptor for k in ["country", "land", "staat", "nation"]):
                    matched = await self._select_country(sel)
                    if matched:
                        filled_fields["country_select"] = "Österreich / Austria"

                # Check Salutation / Anrede dropdown
                elif any(k in combined_descriptor for k in ["anrede", "salutation", "title", "geschlecht"]):
                    matched = await self._select_salutation(sel)
                    if matched:
                        filled_fields["salutation_select"] = self.profile.salutation

                # Check Quiz / Solution dropdown
                elif any(k in combined_descriptor for k in ["antwort", "lösung", "loesung", "solution", "quiz", "frage", "gewinnfrage", "answer", "solutionword"]):
                    matched = await self._select_quiz_solution(sel)
                    if matched:
                        filled_fields["quiz_select"] = self.quiz_answer or "Selected"

            except Exception as e:
                logger.debug(f"Error handling select dropdown: {e}")

        # 3. Handle Radio buttons (Salutation / Anrede)
        radios = await page.locator("input[type='radio']").all()
        for radio in radios:
            try:
                if not await radio.is_visible():
                    continue
                attr_name = (await radio.get_attribute("name") or "").lower()
                attr_val = (await radio.get_attribute("value") or "").lower()
                attr_id = (await radio.get_attribute("id") or "").lower()
                label_text = await self._get_associated_label_text(page, radio, attr_id)
                combined = f"{attr_name} {attr_val} {attr_id} {label_text}".lower()

                if "anrede" in combined or "salutation" in combined or "gender" in combined:
                    if self.profile.salutation.lower() in combined or (self.profile.salutation.lower() == "herr" and ("herr" in combined or "mr" in combined)):
                        await radio.check()
                        filled_fields["salutation_radio"] = self.profile.salutation
            except Exception:
                pass

        # 4. Handle Checkboxes (Strict compliance with Age 18+, T&C, and NO optional newsletters)
        await self._handle_checkboxes(page, filled_fields)

        return filled_fields

    def _match_field_value(self, descriptor: str, input_type: str) -> Optional[str]:
        """Matches a descriptor against personal profile fields or quiz solutions."""
        # Quiz solution match
        if self.quiz_answer and any(k in descriptor for k in ["antwort", "lösung", "loesung", "solution", "quiz", "frage", "gewinnfrage", "answer"]):
            return self.quiz_answer

        # Email
        if input_type == "email" or any(k in descriptor for k in ["email", "e-mail", "mail"]):
            return self.profile.email

        # First Name
        if any(k in descriptor for k in ["vorname", "firstname", "first_name", "first-name", "fname", "givenname"]):
            return self.profile.first_name

        # Last Name
        if any(k in descriptor for k in ["nachname", "lastname", "last_name", "last-name", "lname", "familyname", "surname"]):
            return self.profile.last_name

        # Full Name (only if not specifically vorname or nachname)
        if "name" in descriptor and not any(k in descriptor for k in ["vorname", "nachname", "username", "benutzername", "firmenname"]):
            return self.profile.full_name

        # Phone
        if input_type == "tel" or any(k in descriptor for k in ["telefon", "phone", "mobil", "handy", "tel", "cell"]):
            return self.profile.phone

        # Postal code / PLZ
        if any(k in descriptor for k in ["plz", "postleitzahl", "zip", "postal", "postalcode", "postcode"]):
            return self.profile.postal_code

        # City / Ort
        if any(k in descriptor for k in ["stadt", "ort", "city", "wohnort", "town", "locality"]):
            return self.profile.city

        # House number (if standalone)
        if any(k in descriptor for k in ["hausnummer", "house_number", "housenumber", "hnr"]):
            return self.profile.house_number

        # Street (Straße)
        if any(k in descriptor for k in ["straße", "strasse", "street", "adresse", "address"]):
            # If standalone street field exists, fill street; otherwise address line
            if "hausnummer" in descriptor:
                return self.profile.address_line
            return self.profile.street

        # Country
        if any(k in descriptor for k in ["land", "country", "staat"]):
            return self.profile.country

        # Birth date
        if self.profile.birth_date and any(k in descriptor for k in ["geburt", "birth", "dob", "bday"]):
            return self.profile.birth_date

        return None

    async def _handle_checkboxes(self, page: Page, filled_fields: Dict[str, str]) -> None:
        """Processes checkboxes according to strict compliance rules:
        - CHECK: Age confirmation (18+), Terms of Service, Privacy Policy.
        - LEAVE UNCHECKED: Marketing newsletters, advertising, third-party offers (unless mandatory required).
        """
        checkboxes = await page.locator("input[type='checkbox']").all()

        for cb in checkboxes:
            try:
                attr_id = (await cb.get_attribute("id") or "").lower()
                attr_name = (await cb.get_attribute("name") or "").lower()
                is_required = await cb.get_attribute("required") is not None
                label_text = await self._get_associated_label_text(page, cb, attr_id)
                combined = f"{attr_id} {attr_name} {label_text}".lower()

                # Rule 1: Age verification (18+)
                is_age = any(term in combined for term in [
                    "18 jahre", "mindestens 18", "volljährig", "over 18", "age 18", "18 years"
                ])

                # Rule 2: Terms and Conditions / Privacy
                is_terms = any(term in combined for term in [
                    "teilnahmebedingung", "agb", "datenschutz", "terms", "rules", "conditions",
                    "ich stimme den", "regeln", "privacy"
                ])

                # Rule 3: Marketing / Newsletter / Advertising
                is_marketing = any(term in combined for term in [
                    "newsletter", "werbung", "werbliche", "marketing", "angebote", "zusendung",
                    "informationen per e-mail", "einverstanden, dass", "partner", "werbezwecke"
                ])

                if is_age or is_terms:
                    # Must check
                    if not await cb.is_checked():
                        await cb.check(force=True)
                        logger.debug(f"Checked mandatory checkbox: {label_text[:40]}")
                        filled_fields[f"checkbox_terms_{attr_id or attr_name}"] = "Checked"

                elif is_marketing:
                    # Explicitly leave unchecked unless strictly required by HTML attribute
                    if is_required:
                        logger.debug("Marketing checkbox is marked 'required' by form. Checking...")
                        if not await cb.is_checked():
                            await cb.check(force=True)
                    else:
                        # Ensure it stays unchecked
                        if await cb.is_checked():
                            logger.info(f"Unchecking pre-checked marketing checkbox: {label_text[:40]}")
                            await cb.uncheck(force=True)

                elif is_required:
                    # General mandatory field required to submit
                    if not await cb.is_checked():
                        await cb.check(force=True)
                        logger.debug(f"Checked required form checkbox: {label_text[:40]}")

            except Exception as e:
                logger.debug(f"Error handling checkbox: {e}")

    async def _select_country(self, sel: Locator) -> bool:
        """Attempts to select Austria / Österreich in a select element."""
        options = await sel.locator("option").all()
        for opt in options:
            text = (await opt.inner_text()).lower()
            val = (await opt.get_attribute("value") or "").lower()
            if any(term in text or term in val for term in ["österreich", "oesterreich", "austria", "aut", "at"]):
                opt_val = await opt.get_attribute("value")
                if opt_val is not None:
                    await sel.select_option(value=opt_val)
                else:
                    await sel.select_option(label=await opt.inner_text())
                return True
        return False

    async def _select_salutation(self, sel: Locator) -> bool:
        """Selects salutation (Herr/Frau) in a select element."""
        options = await sel.locator("option").all()
        target = self.profile.salutation.lower()
        for opt in options:
            text = (await opt.inner_text()).lower()
            val = (await opt.get_attribute("value") or "").lower()
            if target in text or target in val:
                opt_val = await opt.get_attribute("value")
                if opt_val is not None:
                    await sel.select_option(value=opt_val)
                else:
                    await sel.select_option(label=await opt.inner_text())
                return True
        return False

    async def _select_quiz_solution(self, sel: Locator) -> bool:
        """Selects the quiz solution from a select dropdown."""
        options = await sel.locator("option").all()
        clean_target = (self.quiz_answer or "").strip().lower()
        for opt in options:
            text = (await opt.inner_text()).strip().lower()
            val = (await opt.get_attribute("value") or "").strip().lower()
            if not text and not val:
                continue
            if clean_target and (clean_target in text or clean_target in val or text in clean_target):
                opt_val = await opt.get_attribute("value")
                if opt_val is not None:
                    await sel.select_option(value=opt_val)
                else:
                    await sel.select_option(label=await opt.inner_text())
                logger.debug(f"Selected quiz solution '{self.quiz_answer}' in select dropdown")
                return True
        return False

    async def _get_associated_label_text(self, page: Page, el: Locator, element_id: str) -> str:
        """Finds label text linked by 'for' attribute or parent container."""
        label_text = ""
        try:
            if element_id:
                lbl = page.locator(f"label[for='{element_id}']")
                if await lbl.count() > 0:
                    label_text = await lbl.first.inner_text()
                    if label_text.strip():
                        return label_text.strip()

            # Check enclosing label or parent div
            parent = el.locator("xpath=..")
            if await parent.count() > 0:
                p_text = await parent.first.inner_text()
                if len(p_text.strip()) < 120:
                    return p_text.strip()
        except Exception:
            pass
        return label_text

    async def find_submit_button(self, page: Page) -> Optional[Locator]:
        """Finds the primary submission button, prioritizing contest triggers over search/nav buttons."""
        submit_patterns = [
            "button:has-text('Jetzt teilnehmen')",
            "button:has-text('Teilnehmen')",
            "button:has-text('Jetzt mitmachen')",
            "button:has-text('Mitmachen')",
            "button:has-text('Absenden')",
            "button:has-text('Eintragen')",
            "button:has-text('Formular absenden')",
            "button:has-text('Submit')",
            "button:has-text('Enter')",
            "input[type='submit']",
            "button[type='submit']",
            "a:has-text('Jetzt teilnehmen')",
            "a:has-text('Teilnehmen')",
        ]

        for sel in submit_patterns:
            try:
                locator = page.locator(sel)
                count = await locator.count()
                for i in range(count):
                    btn = locator.nth(i)
                    if not (await btn.is_visible() and await btn.is_enabled()):
                        continue

                    # Filter out search bar buttons and navigation search icons
                    title = (await btn.get_attribute("title") or "").lower()
                    aria = (await btn.get_attribute("aria-label") or "").lower()
                    cls = (await btn.get_attribute("class") or "").lower()
                    track_id = (await btn.get_attribute("data-track-id") or "").lower()
                    btn_text = (await btn.inner_text()).lower()

                    if any(k in [title, aria, track_id] for k in ["suchen", "search", "searchbutton"]):
                        continue
                    if "search" in cls and not any(k in btn_text for k in ["teilnehmen", "absenden", "mitmachen", "eintragen"]):
                        continue

                    return btn
            except Exception:
                continue

        return None
