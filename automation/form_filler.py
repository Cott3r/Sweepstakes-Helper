"""Semantic form filler capable of matching and filling Austrian and international sweepstake forms."""

import re
from typing import Dict, List, Optional, Tuple, Union
from playwright.async_api import Page, Frame, Locator

from models.sweepstake import UserProfile
from utils.logger import logger


class FormFiller:
    """Intelligently detects form fields, fills profile details, manages checkboxes, and locates submit triggers."""

    def __init__(self, profile: UserProfile, quiz_answer: Optional[str] = None):
        self.profile = profile
        self.quiz_answer = quiz_answer

    def _get_target_contexts(self, page: Page) -> List[Union[Page, Frame]]:
        """Returns the main page followed by relevant child frames (excluding ad/tracking frames)."""
        contexts: List[Union[Page, Frame]] = [page]
        for f in page.frames:
            if f == page.main_frame:
                continue
            url = f.url.lower()
            if any(ad in url for ad in [
                "doubleclick", "googlesyndication", "googleads", "analytics",
                "facebook.com", "twitter.com", "youtube.com", "adservice"
            ]):
                continue
            contexts.append(f)
        return contexts

    def _parse_birth_date(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extracts (day, month, year) strings from profile birth_date."""
        if not self.profile.birth_date:
            return None, None, None
        bd = self.profile.birth_date.strip()
        m_de = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", bd)
        if m_de:
            return m_de.group(1), m_de.group(2), m_de.group(3)
        m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", bd)
        if m_iso:
            return m_iso.group(3), m_iso.group(2), m_iso.group(1)
        return None, None, None

    async def fill_form(self, page: Page) -> Dict[str, str]:
        """Scans the page and child iframes, filling all recognized inputs semantically.

        Returns:
            Dictionary mapping filled field names to filled values.
        """
        filled_fields: Dict[str, str] = {}
        contexts = self._get_target_contexts(page)

        for context in contexts:
            try:
                await self._fill_context(context, page, filled_fields)
            except Exception as e:
                logger.debug(f"Error filling context {getattr(context, 'url', 'page')}: {e}")

        return filled_fields

    async def _fill_context(
        self,
        context: Union[Page, Frame],
        page: Page,
        filled_fields: Dict[str, str]
    ) -> None:
        """Fills form elements within a specific page or iframe context."""
        # 1. Fill Text / Email / Tel / Number inputs
        inputs = await context.locator(
            "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='checkbox']):not([type='radio']), textarea"
        ).all()

        for field in inputs:
            try:
                if not await field.is_visible() or not await field.is_enabled():
                    continue

                attr_name = (await field.get_attribute("name") or "").lower()
                attr_id = (await field.get_attribute("id") or "").lower()
                attr_placeholder = (await field.get_attribute("placeholder") or "").lower()
                attr_aria = (await field.get_attribute("aria-label") or "").lower()
                input_type = (await field.get_attribute("type") or "text").lower()
                attr_role = (await field.get_attribute("role") or "").lower()
                tabindex = await field.get_attribute("tabindex")

                # Detect honeypots: negative tabindex, common honeypot names, or off-screen positioning
                if tabindex == "-1" or any(hp in attr_name or hp in attr_id for hp in ["hp_", "hpr", "honeypot", "spam_filter"]):
                    logger.debug(f"Skipping potential honeypot field '{attr_name or attr_id}'")
                    continue

                # Skip site-wide search bars
                if input_type == "search" or attr_role == "search":
                    continue
                if any(s in attr_name or s in attr_id or s in attr_placeholder or s in attr_aria for s in [
                    "suchergebnisse", "suchfeld", "suchbegriff", "search", "suche"
                ]):
                    logger.debug(f"Skipping search input '{attr_name or attr_id}'")
                    continue
                if attr_name == "q" or attr_id in ["q", "search", "query"]:
                    logger.debug(f"Skipping search query input '{attr_name or attr_id}'")
                    continue

                box = await field.bounding_box()
                if not box or box["x"] < -100 or box["y"] < -100:
                    logger.debug(f"Skipping off-screen input '{attr_name or attr_id}'")
                    continue

                # Get associated label text if possible
                label_text = await self._get_associated_label_text(context, field, attr_id)
                combined_descriptor = f"{attr_name} {attr_id} {attr_placeholder} {attr_aria} {label_text}".lower()

                # Match semantic categories
                val = self._match_field_value(combined_descriptor, input_type)
                if val is not None:
                    # Only fill if currently empty
                    current_val = await field.input_value()
                    if not current_val:
                        try:
                            await field.fill(val)
                        except Exception:
                            await field.type(val, delay=20)
                        filled_fields[attr_id or attr_name or label_text[:20]] = val
                        logger.debug(f"Filled field '{attr_id or attr_name}' with '{val}'")
            except Exception as e:
                logger.debug(f"Error inspecting input field: {e}")

        # 2. Fill Select Dropdowns (Country, Salutation, Birthdate year/month/day, Quiz/Solution)
        selects = await context.locator("select").all()
        for sel in selects:
            try:
                if not await sel.is_visible() or not await sel.is_enabled():
                    continue

                attr_name = (await sel.get_attribute("name") or "").lower()
                attr_id = (await sel.get_attribute("id") or "").lower()
                label_text = await self._get_associated_label_text(context, sel, attr_id)
                combined_descriptor = f"{attr_name} {attr_id} {label_text}".lower()

                # Check Country dropdown
                if any(k in combined_descriptor for k in ["country", "staat", "nation"]) or re.search(r"\b(land)\b", combined_descriptor):
                    if not any(k in combined_descriptor for k in ["bundesland"]):
                        matched = await self._select_country(sel)
                        if matched:
                            filled_fields["country_select"] = "Österreich / Austria"
                            logger.debug("Selected country 'Österreich / Austria' in dropdown")

                # Check Salutation / Anrede dropdown
                elif any(k in combined_descriptor for k in ["anrede", "salutation", "title", "geschlecht", "gender"]):
                    matched = await self._select_salutation(sel)
                    if matched:
                        filled_fields["salutation_select"] = self.profile.salutation
                        logger.debug(f"Selected salutation '{self.profile.salutation}' in dropdown")

                # Check Quiz / Solution dropdown
                elif any(k in combined_descriptor for k in [
                    "antwort", "lösung", "loesung", "solution", "quiz", "frage", "gewinnfrage", "answer", "solutionword"
                ]):
                    matched = await self._select_quiz_solution(sel)
                    if matched:
                        filled_fields["quiz_select"] = self.quiz_answer or "Selected"

                # Check Birth date day / month / year dropdowns
                elif any(k in combined_descriptor for k in ["geburt", "birth", "bday", "dob", "tag", "monat", "jahr", "day", "month", "year"]):
                    b_day, b_month, b_year = self._parse_birth_date()
                    if b_day and re.search(r"\b(tag|day)\b", combined_descriptor):
                        if await self._select_birth_part(sel, b_day):
                            filled_fields["birth_day_select"] = b_day
                    elif b_month and re.search(r"\b(monat|month)\b", combined_descriptor):
                        if await self._select_birth_part(sel, b_month):
                            filled_fields["birth_month_select"] = b_month
                    elif b_year and re.search(r"\b(jahr|year)\b", combined_descriptor):
                        if await self._select_birth_part(sel, b_year):
                            filled_fields["birth_year_select"] = b_year

            except Exception as e:
                logger.debug(f"Error handling select dropdown: {e}")

        # 3. Handle Radio buttons (Salutation / Anrede AND Quiz / Solution)
        radios = await context.locator("input[type='radio']").all()
        for radio in radios:
            try:
                if not await radio.is_visible():
                    continue
                attr_name = (await radio.get_attribute("name") or "").lower()
                attr_val = (await radio.get_attribute("value") or "").lower()
                attr_id = (await radio.get_attribute("id") or "").lower()
                label_text = await self._get_associated_label_text(context, radio, attr_id)
                combined = f"{attr_name} {attr_val} {attr_id} {label_text}".lower()

                # A. Salutation / Anrede
                if "anrede" in combined or "salutation" in combined or "gender" in combined or "geschlecht" in combined:
                    if self.profile.salutation.lower() in combined or (self.profile.salutation.lower() == "herr" and ("herr" in combined or "mr" in combined)):
                        await radio.check()
                        filled_fields["salutation_radio"] = self.profile.salutation
                        logger.debug(f"Selected salutation radio '{self.profile.salutation}'")

                # B. Quiz Solution
                if self.quiz_answer:
                    clean_ans = self.quiz_answer.strip().lower()
                    clean_label = label_text.strip().lower()
                    clean_val = attr_val.strip().lower()
                    if clean_ans and (clean_ans == clean_label or clean_ans == clean_val or clean_ans in clean_label or clean_ans in combined or clean_label in clean_ans):
                        await radio.check()
                        filled_fields[f"quiz_radio_{attr_name or attr_id}"] = self.quiz_answer
                        logger.debug(f"Selected quiz solution radio '{self.quiz_answer}' for {attr_name or attr_id}")

                # C. Required single-choice radio fallback (e.g. surveys or options without a predefined quiz answer)
                is_req = await radio.get_attribute("required") is not None
                if is_req:
                    group_key = f"radio_group_{attr_name}"
                    if group_key not in filled_fields and not any(k.startswith(f"quiz_radio_{attr_name}") for k in filled_fields):
                        await radio.check()
                        filled_fields[group_key] = label_text or attr_val
                        logger.debug(f"Selected required radio choice '{label_text or attr_val}' for {attr_name}")
            except Exception as e:
                logger.debug(f"Error handling radio button: {e}")

        # 4. Handle Checkboxes (Strict compliance with Age 18+, T&C, and NO optional newsletters)
        await self._handle_checkboxes(context, filled_fields)

    def _match_field_value(self, descriptor: str, input_type: str) -> Optional[str]:
        """Matches a descriptor against personal profile fields or quiz solutions."""
        # 1. Quiz solution match
        if self.quiz_answer and any(k in descriptor for k in ["antwort", "lösung", "loesung", "solution", "quiz", "frage", "gewinnfrage", "answer"]):
            return self.quiz_answer

        # 2. Postal code / PLZ (checked BEFORE email to avoid 'mail' in 'mailingpostalcode' false matches)
        if any(k in descriptor for k in ["plz", "postleitzahl", "zip", "postal", "postalcode", "postcode", "mailingpostalcode"]):
            return self.profile.postal_code

        # 3. Email
        if input_type == "email" or re.search(r"\b(e-?mail|mailadresse|emailadresse|e_mail)\b", descriptor) or any(k in descriptor for k in ["email", "e-mail", "mailadresse", "emailadresse"]):
            return self.profile.email

        # 4. First Name
        if any(k in descriptor for k in ["vorname", "firstname", "first_name", "first-name", "fname", "givenname"]):
            return self.profile.first_name

        # 5. Last Name
        if any(k in descriptor for k in ["nachname", "lastname", "last_name", "last-name", "lname", "familyname", "surname"]):
            return self.profile.last_name

        # 6. Full Name (only if not specifically vorname or nachname)
        if "name" in descriptor and not any(k in descriptor for k in ["vorname", "nachname", "username", "benutzername", "firmenname", "hostname", "domain"]):
            return self.profile.full_name

        # 7. Phone (use regex word boundaries to avoid matching words like 'titel', 'hotel', 'bestellen')
        if input_type == "tel" or re.search(r"\b(telefon|telefonnummer|phone|phonenumber|mobil|mobile|mobilnummer|handy|handynummer|tel|cell|rufnummer)\b", descriptor):
            return self.profile.phone

        # 8. City / Ort (avoid matching 'antwort' or 'lösungswort')
        if any(k in descriptor for k in ["stadt", "wohnort", "city", "town", "locality"]) or re.search(r"\b(ort)\b", descriptor):
            if not any(k in descriptor for k in ["antwort", "lösung", "loesung", "wort", "quiz"]):
                return self.profile.city

        # 9. House number (if standalone)
        if any(k in descriptor for k in ["hausnummer", "house_number", "housenumber", "hnr"]):
            return self.profile.house_number

        # 10. Street (Straße)
        if any(k in descriptor for k in ["straße", "strasse", "street", "adresse", "address"]):
            if "hausnummer" in descriptor:
                return self.profile.address_line
            return self.profile.street

        # 11. Country
        if any(k in descriptor for k in ["country", "staat", "nation"]) or re.search(r"\b(land)\b", descriptor):
            if not any(k in descriptor for k in ["bundesland", "deutschland"]):
                return self.profile.country

        # 12. Split birth date fields (Day, Month, Year)
        b_day, b_month, b_year = self._parse_birth_date()
        if b_day and (any(k in descriptor for k in ["geburtstag", "bday_day", "dob_day", "birth_day"]) or re.search(r"\b(tag|day)\b", descriptor)):
            return b_day

        if b_month and (any(k in descriptor for k in ["geburtsmonat", "bday_month", "dob_month", "birth_month"]) or re.search(r"\b(monat|month)\b", descriptor)):
            return b_month

        if b_year and (any(k in descriptor for k in ["geburtsjahr", "bday_year", "dob_year", "birth_year"]) or re.search(r"\b(jahr|year)\b", descriptor)):
            return b_year

        # 13. Full Birth date
        if self.profile.birth_date and any(k in descriptor for k in ["geburt", "birth", "dob", "bday"]):
            return self.profile.birth_date

        return None

    async def _handle_checkboxes(
        self,
        context: Union[Page, Frame],
        filled_fields: Dict[str, str]
    ) -> None:
        """Processes checkboxes according to strict compliance rules:
        - CHECK: Age confirmation (18+), Terms of Service, Privacy Policy.
        - LEAVE UNCHECKED: Marketing newsletters, advertising, third-party offers (unless mandatory required).
        """
        checkboxes = await context.locator("input[type='checkbox']").all()

        for cb in checkboxes:
            try:
                # Skip non-visible checkboxes (avoids Playwright 'Element is not visible' errors)
                if not await cb.is_visible():
                    continue

                attr_id = (await cb.get_attribute("id") or "").lower()
                attr_name = (await cb.get_attribute("name") or "").lower()
                attr_role = (await cb.get_attribute("role") or "").lower()
                attr_cls = (await cb.get_attribute("class") or "").lower()

                # Skip cookie consent manager switches/checkboxes (OneTrust, Usercentrics, Cookiebot, Didomi)
                if attr_role == "switch" or any(c in attr_id or c in attr_name or c in attr_cls for c in [
                    "ot-group", "usercentrics", "cookiebot", "didomi", "consent", "optanon"
                ]):
                    continue

                is_required = await cb.get_attribute("required") is not None
                label_text = await self._get_associated_label_text(context, cb, attr_id)
                combined = f"{attr_id} {attr_name} {label_text}".lower()

                # Rule 1: Age verification (18+)
                is_age = any(term in combined for term in [
                    "18 jahre", "mindestens 18", "volljährig", "over 18", "age 18", "18 years"
                ])

                # Rule 2: Terms and Conditions / Participation Rules
                is_terms = any(term in combined for term in [
                    "teilnahmebedingung", "agb", "terms", "rules", "conditions",
                    "ich stimme den", "regeln", "teilnahme", "gewinnspielbedingungen"
                ])

                # Rule 3: Marketing / Newsletter / Advertising / Commercial opt-ins
                is_marketing = any(term in combined for term in [
                    "newsletter", "werbung", "werbliche", "marketing", "angebote", "zusendung",
                    "informationen per e-mail", "einverstanden, dass", "partner", "werbezwecke",
                    "opt_in", "optin", "promotions", "updates"
                ])

                # Rule 4: Privacy-only consent (not marketing opt-in)
                is_privacy_only = any(term in combined for term in ["datenschutz", "privacy"]) and not is_marketing

                if is_marketing:
                    # Marketing / Newsletter: leave UNCHECKED unless strictly required by the form
                    if is_required:
                        logger.debug("Marketing checkbox is marked 'required' by form. Checking...")
                        if not await cb.is_checked():
                            await self._safe_set_checkbox(cb, True)
                    else:
                        # Ensure it stays unchecked
                        if await cb.is_checked():
                            logger.info(f"Unchecking pre-checked marketing checkbox: {label_text[:40]}")
                            await self._safe_set_checkbox(cb, False)

                elif is_age or is_terms or is_privacy_only:
                    # Must check mandatory terms / age / privacy
                    if not await cb.is_checked():
                        await self._safe_set_checkbox(cb, True)
                        logger.debug(f"Checked mandatory checkbox: {label_text[:40]}")
                        filled_fields[f"checkbox_terms_{attr_id or attr_name}"] = "Checked"

                elif is_required:
                    # General mandatory field required to submit
                    if not await cb.is_checked():
                        await self._safe_set_checkbox(cb, True)
                        logger.debug(f"Checked required form checkbox: {label_text[:40]}")

            except Exception as e:
                logger.debug(f"Error handling checkbox: {e}")

    async def _safe_set_checkbox(self, cb: Locator, check: bool) -> None:
        """Safely checks or unchecks a checkbox, with JavaScript fallback for custom styled elements."""
        try:
            if check:
                await cb.check(force=True, timeout=2000)
            else:
                await cb.uncheck(force=True, timeout=2000)
        except Exception:
            target_bool = "true" if check else "false"
            await cb.evaluate(
                f"el => {{ el.checked = {target_bool}; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}"
            )

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

    async def _select_birth_part(self, sel: Locator, val: str) -> bool:
        """Selects day, month, or year in a birth date dropdown."""
        clean_val = val.lstrip("0")
        options = await sel.locator("option").all()
        for opt in options:
            opt_val = (await opt.get_attribute("value") or "").strip()
            opt_text = (await opt.inner_text()).strip()
            if opt_val in (val, clean_val) or opt_text in (val, clean_val):
                if opt_val is not None:
                    await sel.select_option(value=opt_val)
                else:
                    await sel.select_option(label=opt_text)
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

    async def _get_associated_label_text(
        self,
        context: Union[Page, Frame],
        el: Locator,
        element_id: str
    ) -> str:
        """Finds label text linked by 'for' attribute or parent container."""
        label_text = ""
        try:
            if element_id:
                lbl = context.locator(f"label[for='{element_id}']")
                if await lbl.count() > 0:
                    label_text = await lbl.first.inner_text()
                    if label_text.strip():
                        return label_text.strip()

            # Check enclosing label or parent container
            parent = el.locator("xpath=..")
            if await parent.count() > 0:
                p_text = await parent.first.inner_text()
                if len(p_text.strip()) < 120:
                    return p_text.strip()
        except Exception:
            pass
        return label_text

    async def find_submit_button(self, page: Page) -> Optional[Locator]:
        """Finds the primary submission button across page and child iframes, prioritizing contest triggers."""
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

        contexts = self._get_target_contexts(page)
        for context in contexts:
            for sel in submit_patterns:
                try:
                    locator = context.locator(sel)
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
                        btn_val = (await btn.get_attribute("value") or "").lower()

                        if any(k in [title, aria, track_id] for k in ["suchen", "search", "searchbutton"]):
                            continue
                        if "search" in cls and not any(k in btn_text or k in btn_val for k in ["teilnehmen", "absenden", "mitmachen", "eintragen"]):
                            continue

                        return btn
                except Exception:
                    continue

        return None
