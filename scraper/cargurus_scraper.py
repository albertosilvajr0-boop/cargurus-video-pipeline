"""CarGurus inventory scraper for San Antonio Dodge.

Uses Playwright for browser automation to handle JavaScript-rendered content.
Extracts vehicle details, photos, and window sticker links.
"""

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, Browser
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from config import settings
from utils.database import upsert_vehicle, update_vehicle_status

console = Console()


class CarGurusScraper:
    """Scrapes vehicle inventory from a CarGurus dealer page."""
    
    BASE_URL = "https://www.cargurus.com"
    
    def __init__(self, dealer_url: str = None):
        self.dealer_url = dealer_url or settings.CARGURUS_DEALER_URL
        self.browser: Browser = None
        self.vehicles = []
    
    async def start_browser(self):
        """Launch headless browser."""
        pw = await async_playwright().start()
        self.browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        console.print("[green]✓ Browser launched[/green]")
    
    async def close_browser(self):
        """Close the browser."""
        if self.browser:
            await self.browser.close()
    
    async def scrape_inventory(self, max_vehicles: int = 0) -> list[dict]:
        """
        Scrape all vehicle listings from the dealer's CarGurus page.
        
        Args:
            max_vehicles: Maximum number of vehicles to scrape (0 = all)
            
        Returns:
            List of vehicle dictionaries
        """
        await self.start_browser()
        
        try:
            context = await self.browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()
            
            console.print(f"[cyan]Navigating to dealer page...[/cyan]")
            await page.goto(self.dealer_url, wait_until="networkidle", timeout=60000)
            
            # Wait for inventory listings to load
            await page.wait_for_timeout(3000)
            
            # Scroll to load all inventory (CarGurus uses lazy loading)
            await self._scroll_to_load_all(page)
            
            # Extract listing URLs and basic info from the inventory page
            listings = await self._extract_listing_cards(page)
            console.print(f"[green]Found {len(listings)} vehicle listings[/green]")
            
            if max_vehicles > 0:
                listings = listings[:max_vehicles]
                console.print(f"[yellow]Processing first {max_vehicles} vehicles[/yellow]")
            
            # Visit each listing page for full details
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("Scraping vehicle details...", total=len(listings))
                
                for listing in listings:
                    try:
                        vehicle = await self._scrape_vehicle_detail(page, listing)
                        if vehicle:
                            # Save to database
                            vehicle_id = upsert_vehicle(vehicle)
                            vehicle["db_id"] = vehicle_id
                            self.vehicles.append(vehicle)
                    except Exception as e:
                        console.print(f"[red]Error scraping {listing.get('url', 'unknown')}: {e}[/red]")
                    
                    progress.update(task, advance=1)
                    await page.wait_for_timeout(1500)  # Be polite - don't hammer the server
            
            await context.close()
            
        finally:
            await self.close_browser()
        
        console.print(f"[green]✓ Successfully scraped {len(self.vehicles)} vehicles[/green]")
        return self.vehicles
    
    async def _scroll_to_load_all(self, page: Page):
        """Scroll the page to trigger lazy loading of all inventory cards."""
        console.print("[dim]Scrolling to load all inventory...[/dim]")
        
        previous_count = 0
        scroll_attempts = 0
        max_scrolls = 30
        
        while scroll_attempts < max_scrolls:
            # Count current listing cards
            count = await page.evaluate("""
                () => document.querySelectorAll('[data-cg-ft="car-blade-link"], .pazLpc, a[href*="/inventorylisting/"]').length
            """)
            
            if count == previous_count and scroll_attempts > 3:
                # Check for "Show More" or "Load More" button
                load_more = await page.query_selector(
                    'button:has-text("Show More"), button:has-text("Load More"), '
                    'button:has-text("Next"), a:has-text("Next")'
                )
                if load_more:
                    await load_more.click()
                    await page.wait_for_timeout(2000)
                else:
                    break
            
            previous_count = count
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            scroll_attempts += 1
        
        console.print(f"[dim]Loaded {previous_count} listing cards[/dim]")
    
    async def _extract_listing_cards(self, page: Page) -> list[dict]:
        """Extract basic info and URLs from inventory listing cards."""
        listings = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll(
                    'a[href*="/Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action"], '  +
                    'a[href*="/inventory/"], ' + 
                    '[data-cg-ft="car-blade-link"]'
                );
                
                const results = [];
                const seen = new Set();
                
                cards.forEach(card => {
                    const href = card.getAttribute('href');
                    if (!href || seen.has(href)) return;
                    seen.add(href);
                    
                    // Try to extract basic info from the card
                    const title = card.querySelector('h4, [data-cg-ft="car-blade-title"], .headingTitle')?.textContent?.trim() || '';
                    const price = card.querySelector('[data-cg-ft="car-blade-price"], .priceSection')?.textContent?.trim() || '';
                    
                    results.push({
                        url: href.startsWith('http') ? href : 'https://www.cargurus.com' + href,
                        title: title,
                        price_text: price,
                    });
                });
                
                return results;
            }
        """)
        
        return listings
    
    async def _scrape_vehicle_detail(self, page: Page, listing: dict) -> dict | None:
        """Scrape full details from an individual vehicle listing page."""
        url = listing["url"]
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            console.print(f"[red]Failed to load {url}: {e}[/red]")
            return None
        
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        
        # Extract vehicle details
        vehicle = {
            "listing_url": url,
            "cargurus_id": self._extract_cargurus_id(url),
        }
        
        # --- Title / Year / Make / Model ---
        title_el = soup.select_one("h1, [data-cg-ft='car-listing-title']")
        if title_el:
            title = title_el.get_text(strip=True)
            parsed = self._parse_title(title)
            vehicle.update(parsed)
        
        # --- Price ---
        price_el = soup.select_one(
            "[data-cg-ft='car-listing-price'], .priceSection, "
            "[class*='price'], [data-testid*='price']"
        )
        if price_el:
            price_text = price_el.get_text(strip=True)
            vehicle["price"] = self._parse_price(price_text)
        
        # --- Mileage ---
        mileage_el = soup.find(string=re.compile(r"[\d,]+ miles?", re.I))
        if mileage_el:
            match = re.search(r"([\d,]+)\s*miles?", mileage_el, re.I)
            if match:
                vehicle["mileage"] = int(match.group(1).replace(",", ""))
        
        # --- Key specs from the details section ---
        specs = await self._extract_specs(page)
        vehicle.update(specs)
        
        # --- Photo URLs ---
        photo_urls = await self._extract_photo_urls(page)
        vehicle["photo_urls"] = json.dumps(photo_urls)
        
        # --- Window Sticker ---
        sticker_url = await self._find_window_sticker(page)
        vehicle["sticker_url"] = sticker_url
        
        # --- VIN ---
        vin_el = soup.find(string=re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"))
        if vin_el:
            match = re.search(r"\b([A-HJ-NPR-Z0-9]{17})\b", vin_el)
            if match:
                vehicle["vin"] = match.group(1)
        
        return vehicle
    
    async def _extract_specs(self, page: Page) -> dict:
        """Extract vehicle specifications from the detail page."""
        specs = await page.evaluate("""
            () => {
                const result = {};
                
                // Look for spec rows (label: value pairs)
                const specItems = document.querySelectorAll(
                    '[class*="spec"] dt, [class*="spec"] dd, ' +
                    '[class*="detail"] dt, [class*="detail"] dd, ' +
                    'tr td, [class*="keyFact"]'
                );
                
                // Also check for common spec patterns
                const allText = document.body.innerText;
                
                const patterns = {
                    exterior_color: /Exterior\\s*(?:Color)?\\s*[:：]?\\s*([\\w\\s]+?)(?:\\n|$)/i,
                    interior_color: /Interior\\s*(?:Color)?\\s*[:：]?\\s*([\\w\\s]+?)(?:\\n|$)/i,
                    engine: /Engine\\s*[:：]?\\s*(.+?)(?:\\n|$)/i,
                    transmission: /Transmission\\s*[:：]?\\s*(.+?)(?:\\n|$)/i,
                    drivetrain: /Drivetrain\\s*[:：]?\\s*(.+?)(?:\\n|$)/i,
                };
                
                for (const [key, pattern] of Object.entries(patterns)) {
                    const match = allText.match(pattern);
                    if (match) {
                        result[key] = match[1].trim().substring(0, 100);
                    }
                }
                
                return result;
            }
        """)
        return specs
    
    async def _extract_photo_urls(self, page: Page) -> list[str]:
        """Extract all vehicle photo URLs from the listing page."""
        photos = await page.evaluate("""
            () => {
                const urls = new Set();
                
                // Gallery images
                const imgs = document.querySelectorAll(
                    '[class*="gallery"] img, [class*="photo"] img, ' +
                    '[class*="carousel"] img, [class*="media"] img, ' +
                    'img[src*="cargurus"], img[data-src*="cargurus"]'
                );
                
                imgs.forEach(img => {
                    let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
                    // Get the highest resolution version
                    src = src.replace(/\\/t_/, '/').replace(/_\\d+x\\d+/, '');
                    if (src && !src.includes('logo') && !src.includes('icon') && !src.includes('placeholder')) {
                        urls.add(src);
                    }
                });
                
                return [...urls];
            }
        """)
        return photos
    
    async def _find_window_sticker(self, page: Page) -> str | None:
        """Try to find a window sticker link on the listing page."""
        sticker_url = await page.evaluate("""
            () => {
                // Look for window sticker links
                const links = document.querySelectorAll('a');
                for (const link of links) {
                    const text = (link.textContent || '').toLowerCase();
                    const href = link.getAttribute('href') || '';
                    
                    if (text.includes('window sticker') || 
                        text.includes('monroney') ||
                        href.includes('windowsticker') ||
                        href.includes('window-sticker') ||
                        href.includes('monroney')) {
                        return href.startsWith('http') ? href : null;
                    }
                }
                
                // Check for embedded window sticker images
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const alt = (img.getAttribute('alt') || '').toLowerCase();
                    const src = img.getAttribute('src') || '';
                    if (alt.includes('window sticker') || alt.includes('monroney') ||
                        src.includes('windowsticker') || src.includes('monroney')) {
                        return src;
                    }
                }
                
                return null;
            }
        """)
        return sticker_url
    
    def _extract_cargurus_id(self, url: str) -> str:
        """Extract a unique identifier from the CarGurus URL."""
        # Try to find listing ID in URL parameters
        match = re.search(r"inventoryListing[Ii]d[=:](\d+)", url)
        if match:
            return f"cg_{match.group(1)}"
        
        # Try VDP path
        match = re.search(r"/vdp/(\d+)", url)
        if match:
            return f"cg_{match.group(1)}"
        
        # Fallback: hash the URL
        import hashlib
        return f"cg_{hashlib.md5(url.encode()).hexdigest()[:12]}"
    
    def _parse_title(self, title: str) -> dict:
        """Parse a vehicle title like '2024 Ram 1500 Big Horn' into components."""
        result = {}
        
        # Match: YEAR MAKE MODEL TRIM
        match = re.match(r"(\d{4})\s+(\w+)\s+(.+)", title)
        if match:
            result["year"] = int(match.group(1))
            result["make"] = match.group(2)
            
            # Split remaining into model and trim
            remaining = match.group(3).strip()
            parts = remaining.split(None, 1)
            result["model"] = parts[0] if parts else remaining
            result["trim"] = parts[1] if len(parts) > 1 else ""
        
        return result
    
    def _parse_price(self, price_text: str) -> float | None:
        """Parse a price string like '$34,995' into a float."""
        match = re.search(r"[\$]?([\d,]+)", price_text)
        if match:
            return float(match.group(1).replace(",", ""))
        return None


async def run_scraper(max_vehicles: int = 0) -> list[dict]:
    """Convenience function to run the scraper."""
    scraper = CarGurusScraper()
    return await scraper.scrape_inventory(max_vehicles=max_vehicles or settings.MAX_VEHICLES)
