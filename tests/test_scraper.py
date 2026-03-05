"""Tests for the CarGurus scraper module."""

import re

import pytest

playwright = pytest.importorskip("playwright", reason="playwright not installed")
from scraper.cargurus_scraper import CarGurusScraper


class TestCarGurusScraper:
    def test_parse_title_full(self):
        scraper = CarGurusScraper()
        result = scraper._parse_title("2024 Ram 1500 Big Horn Quad Cab")
        assert result["year"] == 2024
        assert result["make"] == "Ram"
        assert result["model"] == "1500"
        assert "Big Horn" in result["trim"]

    def test_parse_title_no_trim(self):
        scraper = CarGurusScraper()
        result = scraper._parse_title("2023 Jeep Wrangler")
        assert result["year"] == 2023
        assert result["make"] == "Jeep"
        assert result["model"] == "Wrangler"
        assert result["trim"] == ""

    def test_parse_title_invalid(self):
        scraper = CarGurusScraper()
        result = scraper._parse_title("Call for pricing")
        assert result == {}

    def test_parse_price_normal(self):
        scraper = CarGurusScraper()
        assert scraper._parse_price("$42,995") == 42995.0

    def test_parse_price_no_dollar(self):
        scraper = CarGurusScraper()
        assert scraper._parse_price("42995") == 42995.0

    def test_parse_price_invalid(self):
        scraper = CarGurusScraper()
        assert scraper._parse_price("Call for price") is None

    def test_extract_cargurus_id_from_listing_url(self):
        scraper = CarGurusScraper()
        url = "https://www.cargurus.com/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action?inventoryListingId=123456"
        cg_id = scraper._extract_cargurus_id(url)
        assert cg_id == "cg_123456"

    def test_extract_cargurus_id_from_vdp_url(self):
        scraper = CarGurusScraper()
        url = "https://www.cargurus.com/Cars/vdp/789012"
        cg_id = scraper._extract_cargurus_id(url)
        assert cg_id == "cg_789012"

    def test_extract_cargurus_id_fallback_hash(self):
        scraper = CarGurusScraper()
        url = "https://www.cargurus.com/some/random/path"
        cg_id = scraper._extract_cargurus_id(url)
        assert cg_id.startswith("cg_")
        assert len(cg_id) == 15  # cg_ + 12 hex chars


class TestScriptGenerator:
    """Test script generator helper methods (no API calls)."""

    def test_script_prompt_template_format(self):
        """Verify the prompt template can be formatted with all fields."""
        from scripts.script_generator import SCRIPT_PROMPT_TEMPLATE

        prompt = SCRIPT_PROMPT_TEMPLATE.format(
            year=2024,
            make="Ram",
            model="1500",
            trim="Big Horn",
            price=42995.0,
            mileage=12500,
            exterior_color="White",
            interior_color="Black",
            engine="5.7L V8",
            transmission="8-Speed Auto",
            drivetrain="4WD",
            dealer_name="Test Dealer",
        )
        assert "2024" in prompt
        assert "Ram" in prompt
        assert "1500" in prompt
        assert "42,995" in prompt
