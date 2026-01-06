#!/usr/bin/env python3
"""
CryptoRank Universal Scraper
A production-ready terminal-based scraper for any CryptoRank page.

Usage:
    python scraper.py <url>
    python scraper.py https://cryptorank.io/price/kucoin-shares
    python scraper.py https://cryptorank.io/incubators/y-combinator
"""

import json
import sys
import argparse
import time
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup


class CryptoRankScraper:
    """Universal scraper for CryptoRank pages with Cloudflare bypass."""
    
    def __init__(self, headless=True, timeout=60000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.page = None
    
    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--disable-setuid-sandbox',
                '--no-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials',
            ]
        )
        
        # Create context with realistic browser fingerprint
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["geolocation"],
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
            }
        )
        
        # Add stealth scripts
        self.context.add_init_script("""
            // Override navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Override chrome runtime
            window.chrome = {
                runtime: {}
            };
            
            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Override plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)
        
        self.page = self.context.new_page()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
    
    def wait_for_cloudflare(self, max_wait=30):
        """Wait for Cloudflare challenge to complete."""
        print("⏳ Waiting for Cloudflare challenge...")
        
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                # Check if we're past the Cloudflare challenge
                page_content = self.page.content()
                
                # Check for Cloudflare challenge indicators
                if "Just a moment" in page_content or "challenge-platform" in page_content:
                    # Still on challenge page, wait more
                    time.sleep(2)
                    continue
                
                # Check if we have actual content (not just Cloudflare page)
                if len(page_content) > 10000 and "Just a moment" not in page_content:
                    # Likely past the challenge
                    print("✅ Cloudflare challenge passed")
                    return True
                    
            except Exception as e:
                time.sleep(1)
                continue
            
            time.sleep(1)
        
        # Final check
        page_content = self.page.content()
        if "Just a moment" in page_content or "challenge-platform" in page_content:
            print("⚠️  Still on Cloudflare challenge page after waiting")
            return False
        
        return True
    
    def is_noise(self, text):
        """Check if text is noise/UI element."""
        if not text or len(text) < 2:
            return True
        if len(text) > 200:
            return False
        
        noise_patterns = [
            r'^(menu|home|about|contact|login|sign\s*up|search|filter|sort)$',
            r'^(next|previous|load\s*more|show\s*more|see\s*all)$',
            r'^(cookie|privacy|terms|policy)$',
            r'^(\d{1,2}:\d{2}(?::\d{2})?)$',
            r'^(<|>|\+|\-|\×|÷|\=)$',
            r'^(ad|advertisement|sponsored)$',
            r'^(we\s+are\s+hiring!?|join\s+us)$',
            r'^(upgrade|subscribe|premium)$',
            r'^(play\s+now|click\s+here)$',
            r'^(ray\s*id|cloudflare)$',
        ]
        
        import re
        for pattern in noise_patterns:
            if re.match(pattern, text.strip(), re.IGNORECASE):
                return True
        return False
    
    def scrape(self, url):
        """Scrape a CryptoRank URL and return structured data."""
        print(f"🌐 Fetching: {url}")
        
        try:
            # Navigate to page
            self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            
            # Quick check if Cloudflare is present
            page_content = self.page.content()
            has_cloudflare = "Just a moment" in page_content or "challenge-platform" in page_content
            
            # Only wait for Cloudflare if it's actually present
            if has_cloudflare:
                if not self.wait_for_cloudflare():
                    print("⚠️  Warning: May still be on Cloudflare challenge page")
            else:
                # Just wait for JavaScript to render (no Cloudflare)
                self.page.wait_for_timeout(3000)
            
            # Scroll to load content
            print("📜 Scrolling to load content...")
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(2000)
            self.page.evaluate("window.scrollTo(0, 0)")
            self.page.wait_for_timeout(1000)
            
            # Final check if we got blocked by Cloudflare (only warn if actually Cloudflare)
            html = self.page.content()
            if "Just a moment" in html or "challenge-platform" in html:
                print("❌ Error: Still blocked by Cloudflare")
                print("💡 Try running with --no-headless to see what's happening")
                return None
            
            soup = BeautifulSoup(html, 'lxml')
            
            print("📊 Extracting data...")
            
            data = {
                "scraped_at": datetime.now().isoformat(),
                "url": url,
                "page_title": soup.title.string if soup.title else None,
                "main_title": None,
                "subtitle": None,
                "description": None,
                "stats": [],
                "tables": [],
                "cards": [],
                "links": [],
                "lists": [],
                "text_blocks": []
            }
            
            # Main title
            h1 = soup.find('h1')
            if h1:
                title_text = h1.get_text(strip=True)
                # Skip Cloudflare titles
                if title_text.lower() not in ['just a moment...', 'cryptorank.io']:
                    data["main_title"] = title_text
            
            # Subtitle
            h2 = soup.find('h2')
            if h2:
                data["subtitle"] = h2.get_text(strip=True)
            
            # Extract tables
            tables = soup.find_all('table')
            for table_idx, table in enumerate(tables):
                table_data = {
                    "table_index": table_idx,
                    "headers": [],
                    "rows": []
                }
                
                # Headers - look in thead first, then first row
                thead = table.find('thead')
                if thead:
                    header_cells = thead.find_all(['th', 'td'])
                    for header in header_cells:
                        header_text = header.get_text(strip=True)
                        # Clean up header text (remove "Click to sort" etc)
                        header_text = re.sub(r'\(Click to sort.*?\)', '', header_text, flags=re.IGNORECASE).strip()
                        if header_text and not self.is_noise(header_text) and len(header_text) < 100:
                            table_data["headers"].append(header_text)
                
                # If no headers from thead, try first row
                if not table_data["headers"]:
                    first_row = table.find('tr')
                    if first_row:
                        first_cells = first_row.find_all(['th', 'td'])
                        for cell in first_cells:
                            header_text = cell.get_text(strip=True)
                            header_text = re.sub(r'\(Click to sort.*?\)', '', header_text, flags=re.IGNORECASE).strip()
                            if header_text and not self.is_noise(header_text) and len(header_text) < 100:
                                table_data["headers"].append(header_text)
                
                # Rows - skip header row if headers were found
                rows = table.find_all('tr')
                start_idx = 1 if thead or (table_data["headers"] and len(table_data["headers"]) > 0) else 0
                
                for row_idx, row in enumerate(rows[start_idx:start_idx+100]):  # Limit rows
                    cells = row.find_all(['td', 'th'])
                    if not cells:
                        continue
                    
                    # Skip if this looks like a header row (all cells are short and similar)
                    if len(cells) > 2:
                        cell_texts = [c.get_text(strip=True) for c in cells]
                        if all(len(t) < 30 for t in cell_texts) and len(set(cell_texts)) == len(cell_texts):
                            # Might be a header row, skip it
                            continue
                    
                    row_data = {
                        "row_index": row_idx,
                        "cells": []
                    }
                    
                    for cell_idx, cell in enumerate(cells):
                        cell_text = cell.get_text(strip=True)
                        if not cell_text or self.is_noise(cell_text):
                            continue
                        
                        cell_data = {
                            "column_index": cell_idx,
                            "text": cell_text,
                            "header": table_data["headers"][cell_idx] if cell_idx < len(table_data["headers"]) else None
                        }
                        
                        # Extract ALL links (not just first one)
                        links = cell.find_all('a', href=True)
                        cell_data["links"] = []
                        for link in links:
                            href = link.get('href')
                            if href and not href.startswith('#') and 'javascript:void(0)' not in href:
                                if href.startswith('/'):
                                    href = urljoin(url, href)
                                link_text = link.get_text(strip=True)
                                cell_data["links"].append({
                                    "text": link_text or href,
                                    "url": href
                                })
                        
                        # Extract ALL images
                        images = cell.find_all('img')
                        cell_data["images"] = []
                        for img in images:
                            src = img.get('src')
                            if src and 'data:' not in src:
                                if src.startswith('/'):
                                    src = urljoin(url, src)
                                cell_data["images"].append({
                                    "alt": img.get('alt', ''),
                                    "src": src
                                })
                        
                        # Try to extract structured data from cell
                        # Look for patterns like "Name+Description" or "Item1Item2+X"
                        
                        # Extract project/entity name (usually first link text or heading)
                        heading = cell.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b'])
                        if heading:
                            cell_data["heading"] = heading.get_text(strip=True)
                        
                        # Try to extract name from first link if no heading
                        if not cell_data.get("heading") and links:
                            first_link_text = links[0].get_text(strip=True)
                            if first_link_text and len(first_link_text) < 100:
                                cell_data["heading"] = first_link_text
                        
                        # Extract description (longer text, usually after name)
                        paragraphs = cell.find_all('p')
                        if paragraphs:
                            cell_data["description"] = ' '.join([p.get_text(strip=True) for p in paragraphs])
                        
                        # If no paragraphs, try to separate name from description in text
                        if not cell_data.get("description") and cell_text and cell_data.get("heading"):
                            # Remove the heading from text to get description
                            remaining = cell_text.replace(cell_data["heading"], "", 1).strip()
                            if len(remaining) > 50 and len(remaining) < 500:
                                cell_data["description"] = remaining
                        
                        # Extract items from links/images (best method - always try this first)
                        items = []
                        if len(links) > 0:
                            # Extract from link text or image alt
                            for link in links:
                                link_text = link.get_text(strip=True)
                                if link_text and len(link_text) < 100 and link_text not in items:
                                    items.append(link_text)
                            
                            # Also check images for alt text
                            for img in images:
                                img_alt = img.get('alt', '').strip()
                                if img_alt and img_alt not in items and len(img_alt) < 100:
                                    items.append(img_alt)
                        
                        # Parse concatenated items from text (e.g., "Investor1Investor2+X")
                        # Only if we don't have items from links
                        if not items and '+' in cell_text and re.search(r'\+(\d+)', cell_text):
                            # Extract the "+X" count first
                            plus_match = re.search(r'\+(\d+)', cell_text)
                            if plus_match:
                                cell_data["additional_count"] = int(plus_match.group(1))
                                # Remove "+X" from text for parsing
                                text_before_plus = cell_text.split('+')[0]
                            else:
                                text_before_plus = cell_text
                            
                            # Try to split by capital letters (common pattern: "Name1Name2")
                            # Look for places where lowercase is followed by uppercase
                            if re.search(r'[a-z][A-Z]', text_before_plus):
                                # Split on lowercase-to-uppercase boundaries
                                parts = re.split(r'([a-z])([A-Z])', text_before_plus)
                                current_item = ""
                                for i in range(0, len(parts), 3):
                                    if i + 2 < len(parts):
                                        if current_item:
                                            items.append(current_item)
                                        current_item = parts[i] + parts[i+1] + parts[i+2]
                                    elif i < len(parts):
                                        current_item += parts[i]
                                if current_item:
                                    items.append(current_item)
                            
                            # Fallback: split by common separators
                            if not items:
                                parts = re.split(r'[+\n]', text_before_plus)
                                items = [p.strip() for p in parts if p.strip() and not p.strip().isdigit() and len(p.strip()) > 2]
                        
                        # Also check for "+X" pattern even if we have items from links
                        if '+' in cell_text:
                            plus_match = re.search(r'\+(\d+)', cell_text)
                            if plus_match:
                                cell_data["additional_count"] = int(plus_match.group(1))
                        
                        if len(items) > 0:
                            cell_data["items"] = items
                        
                        # Extract monetary values (preserve full format)
                        money_match = re.search(r'\$\s*([\d,.]+[BMK]?)', cell_text)
                        if money_match:
                            # Preserve the original format including spaces
                            full_match = re.search(r'\$\s*[\d,.]+[BMK]?', cell_text)
                            if full_match:
                                cell_data["amount"] = full_match.group(0)
                            else:
                                cell_data["amount"] = '$' + money_match.group(1)
                        
                        # Extract dates
                        date_match = re.search(r'(\w+\s+\d{1,2},\s+\d{4}|\d{1,2}\s+\w+\s+\d{4})', cell_text)
                        if date_match:
                            cell_data["date"] = date_match.group(1)
                        
                        # Extract percentages
                        pct_match = re.search(r'([+-]?\d+\.?\d*)%', cell_text)
                        if pct_match:
                            cell_data["percentage"] = pct_match.group(1) + '%'
                        
                        row_data["cells"].append(cell_data)
                    
                    if row_data["cells"]:
                        table_data["rows"].append(row_data)
                
                if table_data["headers"] or table_data["rows"]:
                    # Also create structured rows (list of dicts with column names as keys)
                    structured_rows = []
                    for row in table_data["rows"]:
                        structured_row = {}
                        for cell in row["cells"]:
                            col_name = cell.get("header") or f"Column_{cell['column_index']}"
                            # Store the full cell data
                            structured_row[col_name] = {
                                "text": cell.get("text", ""),
                                "links": cell.get("links", []),
                                "images": cell.get("images", []),
                                "heading": cell.get("heading"),
                                "description": cell.get("description"),
                                "items": cell.get("items"),
                                "additional_count": cell.get("additional_count"),
                                "amount": cell.get("amount"),
                                "date": cell.get("date"),
                                "percentage": cell.get("percentage")
                            }
                        if structured_row:
                            structured_rows.append(structured_row)
                    
                    table_data["structured_rows"] = structured_rows
                    data["tables"].append(table_data)
            
            # Helper function to validate stat pairs
            def is_valid_stat_pair(label, value):
                """Check if a label-value pair is valid."""
                if not label or not value:
                    return False
                if len(label) > 60 or len(value) > 150:
                    return False
                # Label should have some letters
                if not re.search(r'[a-zA-Z]', label):
                    return False
                # Value should have some content (not just whitespace)
                if not value.strip():
                    return False
                # Skip if both are just numbers
                if re.match(r'^[\d\s]+$', label) and re.match(r'^[\d\s]+$', value):
                    return False
                return True
            
            # Extract label-value pairs
            seen_pairs = set()
            all_elements = soup.find_all(['div', 'span', 'p', 'dt', 'dd', 'li', 'td', 'th'])
            
            for elem in all_elements:
                if not elem or len(elem.find_all()) > 10:
                    continue
                
                text = elem.get_text(strip=True)
                if not text or len(text) < 3 or len(text) > 200:
                    continue
                if self.is_noise(text):
                    continue
                
                # Colon-separated pairs
                if ':' in text:
                    parts = text.split(':', 1)
                    if len(parts) == 2:
                        label = parts[0].strip()
                        value = parts[1].strip()
                        if is_valid_stat_pair(label, value):
                            pair_key = f"{label}|{value}"
                            if pair_key not in seen_pairs:
                                seen_pairs.add(pair_key)
                                data["stats"].append({
                                    "label": label,
                                    "value": value,
                                    "source": "colon_separated"
                                })
                
                # Adjacent sibling pairs - be more selective
                next_sib = elem.find_next_sibling()
                if next_sib:
                    value_text = next_sib.get_text(strip=True)
                    if value_text and value_text != text and len(value_text) < 100:
                        label_ratio = len(re.findall(r'[a-zA-Z]', text)) / len(text) if text else 0
                        value_has_data = bool(re.search(r'[\d$%]|http', value_text))
                        
                        # More strict: label should be mostly text, value should have data
                        # Also check they're not both just labels (like "24h" and "7d")
                        both_are_labels = (
                            len(text) < 10 and len(value_text) < 10 and
                            not re.search(r'[\d$%]', text) and not re.search(r'[\d$%]', value_text)
                        )
                        
                        if label_ratio > 0.4 and value_has_data and not both_are_labels:
                            if is_valid_stat_pair(text, value_text):
                                pair_key = f"{text}|{value_text}"
                                if pair_key not in seen_pairs:
                                    seen_pairs.add(pair_key)
                                    data["stats"].append({
                                        "label": text,
                                        "value": value_text,
                                        "source": "adjacent_sibling"
                                    })
            
            # Extract definition lists
            dts = soup.find_all('dt')
            for dt in dts:
                label = dt.get_text(strip=True)
                dd = dt.find_next_sibling('dd')
                if dd:
                    value = dd.get_text(strip=True)
                    if is_valid_stat_pair(label, value) and not self.is_noise(label) and not self.is_noise(value):
                        pair_key = f"{label}|{value}"
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            data["stats"].append({
                                "label": label,
                                "value": value,
                                "source": "definition_list"
                            })
            
            # Extract cards/items
            card_selectors = [
                '[class*="member"]',  # Team members first
                '[class*="team"]',
                '[class*="person"]',
                '[class*="card"]',
                '[class*="item"]',
                '[class*="company"]',
                '[class*="project"]',
                '[class*="investment"]',
                '[class*="portfolio"]',
                '[class*="round"]'
            ]
            
            # Also look for team member specific patterns
            team_member_patterns = [
                'div[class*="team-member"]',
                'div[class*="member-card"]',
                'div[class*="person-card"]',
                'article[class*="member"]',
                'section[class*="team"]'
            ]
            
            processed_cards = set()
            
            # First, try team member specific selectors
            for selector in team_member_patterns:
                try:
                    items = soup.select(selector)
                    for idx, item in enumerate(items[:50]):
                        if id(item) in processed_cards:
                            continue
                        if len(item.find_all()) > 20:
                            continue
                        
                        item_text = item.get_text(strip=True)
                        if not item_text or len(item_text) < 10:
                            continue
                        
                        card_data = {
                            "index": idx,
                            "text": item_text[:500],
                            "links": [],
                            "images": [],
                            "card_type": "team_member"
                        }
                        
                        # Extract ALL links from team member cards (including nested ones)
                        all_links = item.find_all('a', href=True)
                        for link in all_links:
                            href = link.get('href')
                            if not href or href.startswith('#') or 'javascript:' in href:
                                continue
                            
                            # Make relative URLs absolute
                            if href.startswith('/'):
                                href = urljoin(url, href)
                            
                            link_text = link.get_text(strip=True)
                            if not link_text:
                                link_text = link.get('title') or link.get('aria-label') or ''
                            
                            # Check for social icons
                            img = link.find('img')
                            if img:
                                img_alt = img.get('alt', '')
                                if img_alt and not link_text:
                                    link_text = img_alt
                            
                            card_data["links"].append({
                                "text": link_text or href,
                                "url": href
                            })
                        
                        # Extract images
                        images = item.find_all('img')
                        for img in images[:5]:
                            src = img.get('src')
                            if src and 'data:' not in src:
                                if src.startswith('/'):
                                    src = urljoin(url, src)
                                card_data["images"].append({
                                    "alt": img.get('alt', ''),
                                    "src": src
                                })
                        
                        heading = item.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                        if heading:
                            card_data["heading"] = heading.get_text(strip=True)
                        
                        if card_data["text"] or card_data["links"]:
                            data["cards"].append(card_data)
                            processed_cards.add(id(item))
                except Exception:
                    continue
            
            # Then try general card selectors
            for selector in card_selectors:
                try:
                    items = soup.select(selector)
                    for idx, item in enumerate(items[:50]):  # Limit items
                        if id(item) in processed_cards:
                            continue
                        if len(item.find_all()) > 20:
                            continue
                        
                        item_text = item.get_text(strip=True)
                        if not item_text or len(item_text) < 10:
                            continue
                        
                        card_data = {
                            "index": idx,
                            "text": item_text[:500],
                            "links": [],
                            "images": []
                        }
                        
                        # Extract links - look deeper in the card structure
                        links = item.find_all('a', href=True)
                        for link in links[:10]:  # Limit links
                            href = link.get('href')
                            link_text = link.get_text(strip=True)
                            
                            # Skip empty or invalid links
                            if not href or href.startswith('#') or 'javascript:' in href:
                                continue
                            
                            # Make relative URLs absolute
                            if href.startswith('/'):
                                href = urljoin(url, href)
                            
                            # Get link text from various sources
                            if not link_text:
                                # Try to get text from child elements
                                link_text = link.get('title') or link.get('aria-label') or ''
                            
                            # Also check for social media icons/links
                            img = link.find('img')
                            if img:
                                img_alt = img.get('alt', '')
                                if img_alt:
                                    link_text = link_text or img_alt
                            
                            # Check for data attributes that might indicate link type
                            link_type = None
                            if 'twitter' in href.lower() or 'x.com' in href.lower():
                                link_type = 'twitter'
                            elif 'linkedin' in href.lower():
                                link_type = 'linkedin'
                            elif 'github' in href.lower():
                                link_type = 'github'
                            
                            card_data["links"].append({
                                "text": link_text or href,
                                "url": href,
                                "type": link_type
                            })
                        
                        # Also look for clickable divs/spans with data attributes
                        clickable_elements = item.find_all(['div', 'span'], attrs={'onclick': True})
                        clickable_elements += item.find_all(['div', 'span'], attrs={'data-href': True})
                        clickable_elements += item.find_all(['div', 'span'], attrs={'data-url': True})
                        
                        for elem in clickable_elements[:5]:
                            href = elem.get('data-href') or elem.get('data-url') or elem.get('onclick', '').split("'")[1] if "'" in elem.get('onclick', '') else None
                            if href and not href.startswith('javascript:'):
                                if href.startswith('/'):
                                    href = urljoin(url, href)
                                card_data["links"].append({
                                    "text": elem.get_text(strip=True) or href,
                                    "url": href,
                                    "type": "clickable_element"
                                })
                        
                        # Extract images
                        images = item.find_all('img')
                        for img in images[:5]:  # Limit images
                            src = img.get('src')
                            if src and 'data:' not in src:
                                card_data["images"].append({
                                    "alt": img.get('alt', ''),
                                    "src": src
                                })
                        
                        # Extract heading
                        heading = item.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                        if heading:
                            card_data["heading"] = heading.get_text(strip=True)
                        
                        if card_data["text"] or card_data["links"]:
                            data["cards"].append(card_data)
                            processed_cards.add(id(item))
                except Exception:
                    continue
            
            # Extract links
            important_domains = [
                'twitter.com', 'x.com', 'linkedin.com', 'github.com',
                'telegram', 't.me', 'discord', 'medium.com',
                'facebook.com', 'youtube.com', 'reddit.com',
                'kucoin.com', 'ethereum.org', 'etherscan.io', 'bscscan.com'
            ]
            
            seen_urls = set()
            links = soup.find_all('a', href=True)
            for link in links:
                href = link.get('href')
                if not href or href.startswith('#') or 'javascript:' in href:
                    continue
                if href in seen_urls:
                    continue
                if 'cryptorank.io/plans' in href or 'cryptorank.io/sup' in href:
                    continue
                
                link_text = link.get_text(strip=True)
                if not link_text or len(link_text) > 200:
                    continue
                if self.is_noise(link_text):
                    continue
                
                is_important = any(domain in href.lower() for domain in important_domains)
                looks_meaningful = 3 < len(link_text) < 100
                
                if is_important or looks_meaningful:
                    seen_urls.add(href)
                    data["links"].append({
                        "text": link_text or href,
                        "url": href,
                        "is_important_domain": is_important
                    })
            
            # Extract lists
            lists = soup.find_all(['ul', 'ol'])
            for list_elem in lists:
                if len(list_elem.find_all('li')) < 2:
                    continue
                
                list_items = []
                for li in list_elem.find_all('li')[:50]:  # Limit items
                    text = li.get_text(strip=True)
                    if text and len(text) > 3 and not self.is_noise(text):
                        item_data = {"text": text[:300]}
                        
                        # Extract links from list item
                        item_links = li.find_all('a', href=True)
                        item_data["links"] = [
                            {"text": a.get_text(strip=True), "url": a.get('href')}
                            for a in item_links[:5]
                            if a.get('href') and not a.get('href').startswith('#')
                        ]
                        
                        list_items.append(item_data)
                
                if list_items:
                    data["lists"].append({"items": list_items})
            
            # Extract text blocks
            text_blocks = soup.find_all(['p', lambda tag: tag and 'description' in str(tag.get('class', [])).lower()])
            for block in text_blocks:
                text = block.get_text(strip=True)
                if text and 50 < len(text) < 2000 and not self.is_noise(text):
                    word_count = len(text.split())
                    if word_count > 10:
                        data["text_blocks"].append({
                            "text": text,
                            "length": len(text)
                        })
            
            # Description (first substantial text block)
            if data["text_blocks"]:
                data["description"] = data["text_blocks"][0]["text"][:500]
            
            return data
            
        except PlaywrightTimeout:
            print("❌ Error: Page load timeout")
            return None
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return None


def save_json(data, output_file):
    """Save data as JSON."""
    # Ensure directory exists
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Show relative path if in current directory, absolute otherwise
    try:
        rel_path = os.path.relpath(output_file, os.getcwd())
        if not rel_path.startswith('..'):
            print(f"✅ JSON saved: {rel_path}")
        else:
            print(f"✅ JSON saved: {output_file}")
    except:
        print(f"✅ JSON saved: {output_file}")


def save_csv(data, output_file):
    """Save stats as CSV."""
    if not data.get("stats"):
        print("⚠️  No stats to export as CSV")
        return
    
    import csv
    # Ensure directory exists
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Label', 'Value', 'Source'])
        for stat in data["stats"]:
            writer.writerow([
                stat.get("label", ""),
                stat.get("value", ""),
                stat.get("source", "")
            ])
    
    # Show relative path if in current directory, absolute otherwise
    try:
        rel_path = os.path.relpath(output_file, os.getcwd())
        if not rel_path.startswith('..'):
            print(f"✅ CSV saved: {rel_path}")
        else:
            print(f"✅ CSV saved: {output_file}")
    except:
        print(f"✅ CSV saved: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='CryptoRank Universal Scraper - Scrape any CryptoRank page',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scraper.py https://cryptorank.io/price/kucoin-shares
  python scraper.py https://cryptorank.io/incubators/y-combinator
  python scraper.py https://cryptorank.io/price/bitcoin --output data.json
  python scraper.py https://cryptorank.io/price/ethereum --csv
  python scraper.py https://cryptorank.io/price/kucoin-shares --no-headless
        """
    )
    
    parser.add_argument('url', help='CryptoRank URL to scrape')
    parser.add_argument('-o', '--output', help='Output JSON file (default: auto-generated)')
    parser.add_argument('--csv', action='store_true', help='Also export stats as CSV')
    parser.add_argument('--headless', action='store_true', default=True, help='Run browser in headless mode (default: True)')
    parser.add_argument('--no-headless', dest='headless', action='store_false', help='Show browser window (useful for debugging Cloudflare)')
    parser.add_argument('--timeout', type=int, default=60000, help='Page load timeout in ms (default: 60000)')
    
    args = parser.parse_args()
    
    # Validate URL
    if not args.url.startswith('http'):
        print("❌ Error: URL must start with http:// or https://")
        sys.exit(1)
    
    if 'cryptorank.io' not in args.url:
        print("⚠️  Warning: URL doesn't appear to be a CryptoRank page")
    
    # Generate output filename (save in current working directory)
    if not args.output:
        from urllib.parse import urlparse
        path = urlparse(args.url).path.strip('/')
        filename = path.replace('/', '_') or 'cryptorank_data'
        # Save in current working directory, not script directory
        args.output = os.path.join(os.getcwd(), f"{filename}.json")
    else:
        # If output specified, make sure it's relative to current directory if not absolute
        if not os.path.isabs(args.output):
            args.output = os.path.join(os.getcwd(), args.output)
    
    csv_file = args.output.replace('.json', '_stats.csv') if args.csv else None
    
    print("=" * 60)
    print("🔍 CryptoRank Universal Scraper")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🌐 URL: {args.url}")
    # Show relative path if in current directory
    try:
        rel_output = os.path.relpath(args.output, os.getcwd())
        if not rel_output.startswith('..'):
            print(f"📁 Output: {rel_output}")
        else:
            print(f"📁 Output: {args.output}")
    except:
        print(f"📁 Output: {args.output}")
    if csv_file:
        try:
            rel_csv = os.path.relpath(csv_file, os.getcwd())
            if not rel_csv.startswith('..'):
                print(f"📊 CSV: {rel_csv}")
            else:
                print(f"📊 CSV: {csv_file}")
        except:
            print(f"📊 CSV: {csv_file}")
    print("=" * 60)
    print()
    
    # Scrape
    with CryptoRankScraper(headless=args.headless, timeout=args.timeout) as scraper:
        data = scraper.scrape(args.url)
    
    if not data:
        print("\n❌ Scraping failed")
        print("\n💡 Tips:")
        print("   - Try running with --no-headless to see what's happening")
        print("   - Increase timeout: --timeout 120000")
        print("   - Check your internet connection")
        sys.exit(1)
    
    # Save results
    save_json(data, args.output)
    if args.csv:
        save_csv(data, csv_file)
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"Title: {data.get('main_title', 'N/A')}")
    print(f"Tables: {len(data.get('tables', []))}")
    print(f"Stats: {len(data.get('stats', []))}")
    print(f"Cards: {len(data.get('cards', []))}")
    print(f"Links: {len(data.get('links', []))}")
    print(f"Lists: {len(data.get('lists', []))}")
    print(f"Text Blocks: {len(data.get('text_blocks', []))}")
    print("=" * 60)
    
    if data.get("stats"):
        print("\n📋 Sample Stats (first 5):")
        for stat in data["stats"][:5]:
            print(f"   • {stat['label']}: {stat['value']}")


if __name__ == "__main__":
    main()
