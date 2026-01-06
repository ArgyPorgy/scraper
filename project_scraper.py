#!/usr/bin/env python3


import csv
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


class ProjectScraper:
    """Specialized scraper for project detail pages."""
    
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
            ]
        )
        
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        
        self.page = self.context.new_page()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
    
    def wait_for_cloudflare(self):
        """Wait for Cloudflare challenge if present."""
        try:
            # Check for Cloudflare challenge
            cf_indicators = [
                'Just a moment',
                'Checking your browser',
                'Please wait',
                'cf-browser-verification'
            ]
            
            for _ in range(30):  # Wait up to 30 seconds
                page_text = self.page.content()
                if not any(indicator in page_text for indicator in cf_indicators):
                    time.sleep(1)
                    return True
                time.sleep(1)
            return True
        except:
            return True
    
    def scroll_to_load(self):
        """Scroll page to load dynamic content."""
        self.page.evaluate("""
            async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 100;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        
                        if(totalHeight >= scrollHeight || totalHeight > 5000){
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100);
                });
            }
        """)
        time.sleep(2)
    
    def extract_project_basic_info(self, soup, url):
        """Extract basic project information."""
        info = {
            "name": None,
            "tagline": None,
            "transparency_score": None,
            "website": None,
            "x_handle": None,
            "x_url": None
        }
        
        # Extract project name (usually in h1 or main heading)
        title_elem = soup.find(['h1', 'h2'], class_=re.compile(r'title|name|heading', re.I))
        if not title_elem:
            title_elem = soup.find('h1')
        if title_elem:
            info["name"] = title_elem.get_text(strip=True)
        
        # Extract tagline/subtitle
        tagline_elem = soup.find(string=re.compile(r'Payment Infrastructure|Infrastructure|Platform', re.I))
        if tagline_elem:
            parent = tagline_elem.parent
            if parent:
                info["tagline"] = parent.get_text(strip=True)
        
        # Look for tagline in common patterns
        tagline_selectors = [
            soup.find('p', class_=re.compile(r'subtitle|tagline|description', re.I)),
            soup.find('div', class_=re.compile(r'subtitle|tagline', re.I)),
        ]
        for elem in tagline_selectors:
            if elem:
                text = elem.get_text(strip=True)
                if text and len(text) < 200:
                    info["tagline"] = text
                    break
        
        # Extract transparency score
        transparency_elem = soup.find(string=re.compile(r'Transparency Score|Transparency', re.I))
        if transparency_elem:
            # Look in parent and siblings
            parent = transparency_elem.parent
            for _ in range(3):  # Check up to 3 levels up
                if parent:
                    score_text = parent.get_text()
                    score_match = re.search(r'(\d+)%', score_text)
                    if score_match:
                        info["transparency_score"] = score_match.group(1)
                        break
                    parent = parent.parent
        
        # Extract website
        website_link = soup.find('a', href=re.compile(r'^https?://(?!www\.rootdata|www\.cryptorank)', re.I))
        if website_link:
            href = website_link.get('href', '')
            if href and not any(domain in href for domain in ['rootdata.com', 'cryptorank.io']):
                info["website"] = href
        
        # Extract X handle and URL (look for @handle pattern or x.com links)
        # First, look for @handle in text (but not in URLs)
        x_handle_match = re.search(r'@([A-Za-z0-9_]+)', soup.get_text())
        if x_handle_match:
            info["x_handle"] = '@' + x_handle_match.group(1)
        
        # Look for x.com or twitter.com links (prioritize these over website links)
        x_links = soup.find_all('a', href=re.compile(r'x\.com|twitter\.com', re.I))
        for x_link in x_links:
            href = x_link.get('href', '')
            if href and ('x.com' in href or 'twitter.com' in href):
                info["x_url"] = href
                # Extract handle from URL
                handle_match = re.search(r'(?:x\.com|twitter\.com)/(?:@)?([^/?]+)', href, re.I)
                if handle_match:
                    handle = handle_match.group(1)
                    info["x_handle"] = '@' + handle if not handle.startswith('@') else handle
                break
        
        return info
    
    def extract_metrics(self, soup):
        """Extract performance metrics."""
        metrics = {
            "rd_popularity_index": None,
            "rd_popularity_change": None,
            "rd_popularity_rank": None,
            "rd_growth_index": None,
            "rd_growth_change": None,
            "rd_growth_rank": None
        }
        
        # Look for RD Popularity Index
        popularity_elem = soup.find(string=re.compile(r'RD Popularity Index|Popularity Index', re.I))
        if popularity_elem:
            parent = popularity_elem.parent
            while parent and parent.name != 'body':
                # Look for numbers nearby
                text = parent.get_text()
                # Extract value
                value_match = re.search(r'(\d+(?:\.\d+)?)', text)
                if value_match:
                    metrics["rd_popularity_index"] = value_match.group(1)
                
                # Extract change percentage
                change_match = re.search(r'([+-]?\d+\.?\d*%)\s*\(24h\)|([+-]?\d+\.?\d*%)\s*\(7d', text)
                if change_match:
                    metrics["rd_popularity_change"] = change_match.group(1) or change_match.group(2)
                
                # Extract rank
                rank_match = re.search(r'Rank[:\s]*(\d+/\d+)', text, re.I)
                if rank_match:
                    metrics["rd_popularity_rank"] = rank_match.group(1)
                
                parent = parent.parent
        
        # Look for RD Growth Index
        growth_elem = soup.find(string=re.compile(r'RD Growth Index|Growth Index', re.I))
        if growth_elem:
            parent = growth_elem.parent
            while parent and parent.name != 'body':
                text = parent.get_text()
                value_match = re.search(r'(\d+(?:\.\d+)?)', text)
                if value_match:
                    metrics["rd_growth_index"] = value_match.group(1)
                
                change_match = re.search(r'([+-]?\d+\.?\d*%)\s*\(24h\)|([+-]?\d+\.?\d*%)\s*\(7d', text)
                if change_match:
                    metrics["rd_growth_change"] = change_match.group(1) or change_match.group(2)
                
                rank_match = re.search(r'Rank[:\s]*(\d+/\d+)', text, re.I)
                if rank_match:
                    metrics["rd_growth_rank"] = rank_match.group(1)
                
                parent = parent.parent
        
        return metrics
    
    def extract_comparison_data(self, soup):
        """Extract comparison/financial data."""
        comparison = {
            "total_raised": None,
            "total_raised_rank": None,
            "fdv": None,
            "trading_volume_24h": None
        }
        
        # Extract Total Raised
        raised_elem = soup.find(string=re.compile(r'Total Raised', re.I))
        if raised_elem:
            parent = raised_elem.parent
            while parent and parent.name != 'body':
                text = parent.get_text()
                # Extract amount
                amount_match = re.search(r'\$([\d,.]+[BMK]?)', text)
                if amount_match:
                    comparison["total_raised"] = '$' + amount_match.group(1)
                
                # Extract rank
                rank_match = re.search(r'Top\s*(\d+)%|Ranked\s*Top\s*(\d+)%', text, re.I)
                if rank_match:
                    comparison["total_raised_rank"] = f"Top {rank_match.group(1) or rank_match.group(2)}%"
                
                parent = parent.parent
        
        # Extract FDV
        fdv_elem = soup.find(string=re.compile(r'^FDV[:]?$', re.I))
        if fdv_elem:
            parent = fdv_elem.parent
            while parent and parent.name != 'body':
                text = parent.get_text()
                if '--' in text or 'not listed' in text.lower():
                    comparison["fdv"] = '--'
                else:
                    fdv_match = re.search(r'\$([\d,.]+[BMK]?)', text)
                    if fdv_match:
                        comparison["fdv"] = '$' + fdv_match.group(1)
                parent = parent.parent
        
        # Extract 24h Trading Volume
        volume_elem = soup.find(string=re.compile(r'24h Trading Volume|Trading Volume', re.I))
        if volume_elem:
            parent = volume_elem.parent
            while parent and parent.name != 'body':
                text = parent.get_text()
                if '--' in text or 'not listed' in text.lower():
                    comparison["trading_volume_24h"] = '--'
                else:
                    volume_match = re.search(r'\$([\d,.]+[BMK]?)', text)
                    if volume_match:
                        comparison["trading_volume_24h"] = '$' + volume_match.group(1)
                parent = parent.parent
        
        return comparison
    
    def extract_team_members(self, soup, url):
        """Extract team members with roles and social links."""
        team = []
        seen_names = set()
        
        # Method 1: Find member profile links (most reliable)
        member_links = soup.find_all('a', href=re.compile(r'/member/', re.I))
        for link in member_links:
            href = link.get('href', '')
            if not href:
                continue
            
            # Convert to absolute URL
            if href.startswith('/'):
                href = urljoin(url, href)
            
            # Extract name from link text or URL
            name = link.get_text(strip=True)
            if not name or len(name) > 100:
                # Try to extract from URL
                name_match = re.search(r'/member/([^/?]+)', href)
                if name_match:
                    name = name_match.group(1).replace('%20', ' ').replace('+', ' ')
            
            # Clean name (remove role if concatenated, e.g., "Peter GlymanFounder" -> "Peter Glyman")
            if name:
                # Remove common role suffixes at the end
                name = re.sub(r'(Founder|CEO|CTO|Chief.*Officer|Director|Manager|Lead|Engineer|Head\s+of.*)$', '', name, flags=re.I).strip()
                # If name contains role words in the middle (likely concatenated like "Peter GlymanFounder")
                if re.search(r'[a-z][A-Z]', name):  # lowercase followed by uppercase (concatenation)
                    # Split on capital letters
                    name_parts = re.split(r'([a-z])([A-Z])', name)
                    if len(name_parts) >= 3:
                        # Reconstruct just the name part (first 2-3 words)
                        clean_name = ""
                        for i in range(0, min(len(name_parts), 6), 3):
                            if i + 2 < len(name_parts):
                                clean_name += name_parts[i] + name_parts[i+1] + name_parts[i+2] + " "
                            elif i < len(name_parts):
                                clean_name += name_parts[i]
                        name = clean_name.strip()
                        # Take first 2-3 words
                        name_words = name.split()
                        if len(name_words) > 3:
                            name = ' '.join(name_words[:3])
            
            if not name or name in seen_names:
                continue
            
            seen_names.add(name)
            
            member = {
                "name": name,
                "role": None,
                "x_url": None,
                "linkedin_url": None,
                "profile_image": None,
                "profile_url": href
            }
            
            # Find the card/container for this member
            card = link.find_parent(['div', 'article', 'section'])
            if not card:
                card = link.parent
            
            # Extract role from nearby text
            card_text = card.get_text() if card else ''
            role_patterns = [
                r'Founder\s+and\s+Chief\s+Executive\s+Officer',
                r'Chief\s+\w+\s+Officer',
                r'Founder',
                r'CEO',
                r'CTO',
                r'Director',
                r'Manager',
                r'Lead',
                r'Engineer',
                r'Head\s+of\s+\w+'
            ]
            
            for pattern in role_patterns:
                role_match = re.search(pattern, card_text, re.I)
                if role_match:
                    member["role"] = role_match.group(0)
                    break
            
            # Extract profile image (look for image with alt containing name or nearby)
            images = card.find_all('img') if card else []
            for img in images:
                src = img.get('src', '')
                alt = img.get('alt', '')
                
                # Skip social icons
                if 'twitter' in src.lower() or 'in.' in src.lower() or 'linkedin' in src.lower():
                    # This is a social icon - find the parent link
                    social_link = img.find_parent('a', href=True)
                    if social_link:
                        social_href = social_link.get('href', '')
                        if social_href.startswith('/'):
                            social_href = urljoin(url, social_href)
                        
                        if 'x.com' in social_href or 'twitter.com' in social_href:
                            member["x_url"] = social_href
                        elif 'linkedin.com' in social_href:
                            member["linkedin_url"] = social_href
                    continue
                
                # This might be a profile image
                if src and (name.lower() in alt.lower() or alt or '128x128' in src or 'jpg' in src.lower() or 'webp' in src.lower()):
                    if src.startswith('/'):
                        src = urljoin(url, src)
                    if not member["profile_image"]:
                        member["profile_image"] = src
            
            # Extract social links from clickable elements with social icons
            if card:
                # Look for links/buttons near social icons
                social_icons = card.find_all('img', src=re.compile(r'twitter|linkedin|in\.', re.I))
                for icon in social_icons:
                    # Find parent link or button
                    parent_link = icon.find_parent('a', href=True)
                    if parent_link:
                        social_href = parent_link.get('href', '')
                        if social_href.startswith('/'):
                            social_href = urljoin(url, social_href)
                        
                        if ('x.com' in social_href or 'twitter.com' in social_href) and not member["x_url"]:
                            member["x_url"] = social_href
                        elif 'linkedin.com' in social_href and not member["linkedin_url"]:
                            member["linkedin_url"] = social_href
            
            team.append(member)
        
        # Deduplicate team members by profile_url or name
        seen_urls = {}
        deduplicated_team = []
        for member in team:
            profile_url = member.get("profile_url")
            name = member.get("name", "").lower()
            
            # Use profile_url as primary key, name as fallback
            key = profile_url if profile_url else name
            
            if key not in seen_urls:
                seen_urls[key] = member
                deduplicated_team.append(member)
            else:
                # Merge data (prefer non-null values)
                existing = seen_urls[key]
                for field in ["role", "x_url", "linkedin_url", "profile_image"]:
                    if not existing.get(field) and member.get(field):
                        existing[field] = member[field]
        
        team = deduplicated_team
        
        # Method 2: Fallback - Look for name-role patterns in text
        if not team:
            role_name_pattern = re.compile(r'(Founder(?:\s+and\s+Chief\s+Executive\s+Officer)?|Chief\s+\w+\s+Officer|CEO|CTO|Director|Manager|Lead|Engineer|Head\s+of\s+\w+)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', re.I)
            matches = role_name_pattern.findall(soup.get_text())
            for role, name in matches:
                if name.strip() not in seen_names:
                    seen_names.add(name.strip())
                    team.append({
                        "name": name.strip(),
                        "role": role.strip(),
                        "x_url": None,
                        "linkedin_url": None,
                        "profile_image": None,
                        "profile_url": None
                    })
        
        return team
    
    def extract_investors(self, soup, url):
        """Extract investors from Fundraising section."""
        investors = []
        seen_names = set()
        
        # Find the Fundraising section - look for the section with "Investors" tab
        fundraising_elem = soup.find(string=re.compile(r'^Fundraising$|Core Investors', re.I))
        fundraising_container = None
        if fundraising_elem:
            # Find parent container
            parent = fundraising_elem.parent
            for _ in range(5):
                if parent:
                    # Look for a container that has "Investors" text nearby (the tab)
                    parent_text = parent.get_text()
                    if 'Investors' in parent_text and 'Rounds' in parent_text:
                        fundraising_container = parent
                        break
                    parent = parent.parent
            if not fundraising_container:
                fundraising_container = fundraising_elem.find_parent(['div', 'section'])
                if not fundraising_container:
                    fundraising_container = fundraising_elem.parent
        
        # Method 1: Extract investors from the specific HTML structure: div.item > a.card
        # Look for div elements with class "item" (these are the investor card containers)
        investor_cards = soup.find_all('div', class_=re.compile(r'\bitem\b', re.I))
        
        for card_div in investor_cards:
            # Check if this div contains an investor link or image
            # Don't be too strict - just check if it has the right structure
            
            investor = {
                "name": None,
                "logo": None,
                "url": None
            }
            
            # First, try to find a link with class "card"
            link = card_div.find('a', class_=re.compile(r'\bcard\b', re.I))
            if link:
                href = link.get('href', '')
                if href and ('/Investors/' in href or '/Projects/detail/' in href):
                    if href.startswith('/'):
                        href = urljoin(url, href)
                    investor["url"] = href
                    
                    # Extract name from link text or URL
                    name = link.get_text(strip=True)
                    if not name or len(name) > 100:
                        name_match = re.search(r'/(?:Investors|Projects)/detail/([^/?]+)', href)
                        if name_match:
                            name = name_match.group(1).replace('%20', ' ').replace('+', ' ')
                    
                    if name:
                        investor["name"] = name
                    
                    # Extract logo from image inside the link
                    img = link.find('img')
                    if img:
                        img_src = img.get('src', '')
                        if img_src and 'default' not in img_src.lower() and 'twitter' not in img_src.lower() and 'linkedin' not in img_src.lower():
                            if img_src.startswith('/'):
                                img_src = urljoin(url, img_src)
                            investor["logo"] = img_src
            
            # If no link or name from link, try to extract from image alt text
            if not investor["name"]:
                img = card_div.find('img', alt=True)
                if img:
                    alt = img.get('alt', '').strip()
                    if alt and len(alt) > 2 and len(alt) < 100 and alt[0].isupper():
                        investor["name"] = alt
                        img_src = img.get('src', '')
                        if img_src and 'default' not in img_src.lower():
                            if img_src.startswith('/'):
                                img_src = urljoin(url, img_src)
                            investor["logo"] = img_src
            
            # Extract name from card text if still not found
            if not investor["name"]:
                card_text_clean = ' '.join(card_div.get_text().split())
                # Look for capitalized words that look like company names
                name_match = re.search(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:Ventures|Capital|Partners|Innovations|Group))?)\b', card_text_clean)
                if name_match:
                    potential_name = name_match.group(1).strip()
                    # Filter out UI elements
                    ui_keywords = ['Fundraising', 'Rounds', 'Investors', 'Button', 'Item', 'Card']
                    if potential_name not in ui_keywords and len(potential_name) > 2 and len(potential_name) < 100:
                        investor["name"] = potential_name
            
            # Only add if we have a valid name and it's not a duplicate
            # Must have either a valid URL (investor/project link) or a logo
            if investor["name"] and investor["name"] not in seen_names:
                # Skip if it's a team member (has /member/ in URL)
                if investor["url"] and '/member/' in investor["url"]:
                    continue
                
                # Must have either a link to investor/project OR a logo (to be a real investor card)
                if not investor["url"] and not investor["logo"]:
                    continue
                
                name_lower = investor["name"].lower()
                # Filter out UI elements and team member names - be very specific
                ui_keywords = ['language', 'currency', 'theme', 'light', 'dark', 'english', 'usd',
                              'all projects', 'market', 'people', 'x data', 'download', 
                              'api', 'points', 'transparency', 'upcoming', 'token', 'new', 
                              'rootdata', 'news', 'dashboard', 'archives', 'deals', 'data']
                # Skip if it's a UI element
                if any(ui == name_lower or (len(ui) > 3 and ui in name_lower) for ui in ui_keywords):
                    continue
                
                # Skip if it looks like a person's name (first name + last name pattern, no company words)
                if re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+$', investor["name"]) and not any(word in investor["name"] for word in ['Ventures', 'Capital', 'Partners', 'Innovations', 'Group', 'Labs']):
                    # Check if URL is to a member page
                    if investor["url"] and '/member/' in investor["url"]:
                        continue
                
                # Must look like a company name (has capital letters, reasonable length)
                if not re.search(r'[A-Z]', investor["name"]) or len(investor["name"]) < 3:
                    continue
                
                seen_names.add(investor["name"])
                investors.append(investor)
        
        # Also check fundraising container if we didn't find enough
        if len(investors) < 2 and fundraising_container:
            # Find all links to investor or project detail pages within fundraising section
            investor_links = fundraising_container.find_all('a', href=re.compile(r'/Investors/|/Projects/detail/', re.I))
            
            for link in investor_links:
                href = link.get('href', '')
                if not href:
                    continue
                
                if href.startswith('/'):
                    href = urljoin(url, href)
                
                # Extract name from link text
                name = link.get_text(strip=True)
                if not name or len(name) > 100:
                    # Try to extract from URL
                    name_match = re.search(r'/(?:Investors|Projects)/detail/([^/?]+)', href)
                    if name_match:
                        name = name_match.group(1).replace('%20', ' ').replace('+', ' ')
                
                if not name or name in seen_names:
                    continue
                
                # Filter out navigation/footer links - be more specific
                nav_keywords = ['all projects', 'market', 'people', 'x data', 'download the', 
                              'api', 'points', 'transparency improvement', 'upcoming events', 
                              'token unlocks', 'new', 'rootdata list']
                link_text_lower = name.lower()
                if any(nav in link_text_lower for nav in nav_keywords):
                    continue
                
                # Check if link is in navigation/footer/header area
                link_parent = link.find_parent(['nav', 'footer', 'header'])
                if link_parent:
                    continue
                
                # Check if the link's parent container looks like navigation
                card = link.find_parent(['div', 'article', 'section'])
                if card:
                    card_text = card.get_text().lower()
                    if any(nav in card_text for nav in ['navigation', 'menu', 'sidebar', 'footer']):
                        continue
                
                investor = {
                    "name": name,
                    "logo": None,
                    "url": href
                }
                
                # Find the card/container for this investor to get logo
                if not card:
                    card = link.parent
                
                if card:
                    # Look for image in the card
                    img = card.find('img')
                    if img:
                        img_src = img.get('src', '')
                        
                        # Skip UI icons but allow investor logos
                        if (img_src and 'default' not in img_src.lower() and 
                            'twitter' not in img_src.lower() and 'linkedin' not in img_src.lower() and
                            'icon' not in img_src.lower() and 'button' not in img_src.lower() and
                            'data:image' not in img_src):
                            # Allow _nuxt/img if it's in public/images (investor logos)
                            if '_nuxt/img' in img_src and 'public' not in img_src:
                                pass  # Skip navigation icons
                            else:
                                if img_src.startswith('/'):
                                    img_src = urljoin(url, img_src)
                                investor["logo"] = img_src
                
                seen_names.add(name)
                investors.append(investor)
        
        # Method 2: Extract investors from images with alt text in fundraising section
        # This is important because some investors might not have links
        if fundraising_container:
            # Look for images with alt text that look like investor names
            investor_images = fundraising_container.find_all('img', alt=True)
            
            for img in investor_images:
                alt = img.get('alt', '').strip()
                src = img.get('src', '')
                
                # Skip if already seen, UI elements, or invalid
                if (not alt or alt in seen_names or len(alt) < 2 or len(alt) > 100 or
                    'default' in src.lower() or 'twitter' in src.lower() or 'linkedin' in src.lower() or
                    'icon' in src.lower() or 'button' in src.lower() or 'data:image' in src or
                    '_nuxt/img' in src):  # Skip navigation icons
                    continue
                
                # Must be capitalized and look like a company name
                if not alt[0].isupper() or not re.search(r'[A-Z]', alt):
                    continue
                
                # Filter out UI elements - be very specific
                ui_keywords = ['fundraising', 'rounds', 'investors list', 'button', 'icon', 'toggle', 
                              'switch', 'language', 'currency', 'theme', 'download', 'api', 'points',
                              'english', 'usd', 'light', 'dark', 'all projects', 'market', 'people',
                              'x data', 'rootdata list', 'new', 'upcoming events', 'token unlocks', 
                              'transparency', 'news', 'dashboard', 'archives', 'deals']
                if any(ui in alt.lower() for ui in ui_keywords):
                    continue
                
                # Check if this image is in navigation/footer
                card = img.find_parent(['div', 'article', 'section', 'a', 'nav', 'footer', 'header'])
                if card:
                    card_tag = card.name.lower()
                    if card_tag in ['nav', 'footer', 'header']:
                        continue
                    card_text = card.get_text().lower()
                    # Skip if it's clearly navigation/footer
                    if any(ui in card_text for ui in ['navigation', 'menu', 'footer', 'header', 'sidebar']):
                        continue
                
                # This looks like an investor - add it
                seen_names.add(alt)
                investor = {
                    "name": alt,
                    "logo": src if src.startswith('http') else urljoin(url, src) if src.startswith('/') else None,
                    "url": None
                }
                
                # Try to find link nearby
                if card and card.name != 'nav' and card.name != 'footer' and card.name != 'header':
                    link = card.find('a', href=True) if card.name != 'a' else card if card.name == 'a' and card.get('href') else None
                    if link and link.name == 'a':
                        href = link.get('href', '')
                        if href.startswith('/'):
                            href = urljoin(url, href)
                        investor["url"] = href
                
                investors.append(investor)
        
        # Method 3: Direct search for investor images near "Fundraising" text (most reliable)
        # Find all images and check if they're near "Fundraising" section
        all_images = soup.find_all('img', alt=True)
        fundraising_text_elem = soup.find(string=re.compile(r'Fundraising', re.I))
        
        if fundraising_text_elem:
            # Get the fundraising section area
            fundraising_area = fundraising_text_elem.parent
            for _ in range(3):
                if fundraising_area:
                    # Find images in this area
                    area_images = fundraising_area.find_all('img', alt=True)
                    for img in area_images:
                        alt = img.get('alt', '').strip()
                        src = img.get('src', '')
                        
                        # Skip if invalid or already seen
                        if (not alt or alt in seen_names or len(alt) < 2 or len(alt) > 100 or
                            not alt[0].isupper() or 'default' in src.lower() or 
                            'twitter' in src.lower() or 'linkedin' in src.lower() or
                            '_nuxt/img' in src or 'data:image' in src):
                            continue
                        
                        # Must look like an investor/company name (has capital letters, reasonable length)
                        if not re.search(r'[A-Z][a-z]+', alt):  # Must have at least one capitalized word
                            continue
                        
                        # Filter out UI elements
                        ui_keywords = ['fundraising', 'rounds', 'investors', 'button', 'icon', 'toggle', 
                                      'switch', 'language', 'currency', 'theme', 'download', 'api', 
                                      'points', 'all projects', 'market', 'people', 'x data', 
                                      'rootdata', 'new', 'upcoming', 'token', 'transparency', 
                                      'news', 'dashboard', 'archives', 'deals']
                        if any(ui in alt.lower() for ui in ui_keywords):
                            continue
                        
                        # Check if image is in nav/footer
                        img_parent = img.find_parent(['nav', 'footer', 'header'])
                        if img_parent:
                            continue
                        
                        seen_names.add(alt)
                        investor = {
                            "name": alt,
                            "logo": src if src.startswith('http') else urljoin(url, src) if src.startswith('/') else None,
                            "url": None
                        }
                        
                        # Try to find link
                        card = img.find_parent(['div', 'article', 'section', 'a'])
                        if card:
                            link = card.find('a', href=True) if card.name != 'a' else card if card.name == 'a' and card.get('href') else None
                            if link and link.name == 'a':
                                href = link.get('href', '')
                                if href.startswith('/'):
                                    href = urljoin(url, href)
                                investor["url"] = href
                        
                        investors.append(investor)
                    break
                fundraising_area = fundraising_area.parent
        
        # Method 4: Also extract from links (as backup)
        investor_links = soup.find_all('a', href=re.compile(r'/Investors/|/Projects/detail/', re.I))
        
        for link in investor_links:
            href = link.get('href', '')
            if not href:
                continue
            
            # Convert to absolute URL
            if href.startswith('/'):
                href = urljoin(url, href)
            
            # Extract name from link text
            name = link.get_text(strip=True)
            if not name or len(name) > 100:
                # Try to extract from URL
                name_match = re.search(r'/(?:Investors|Projects)/detail/([^/?]+)', href)
                if name_match:
                    name = name_match.group(1).replace('%20', ' ').replace('+', ' ')
            
            # Clean name (remove description if concatenated)
            if name:
                # Remove common project description suffixes
                name = re.sub(r'(Crypto-focused|enterprise|payment|platform|infrastructure|solution|network|account).*$', '', name, flags=re.I).strip()
                # If name is too long, likely has description concatenated
                if len(name) > 50:
                    # Try to extract just the name part (usually first 2-3 words)
                    name_parts = name.split()
                    if len(name_parts) > 3:
                        name = ' '.join(name_parts[:3])
            
            # Skip if it's the project itself (we'll check this later in the scrape method)
            if not name or name in seen_names:
                continue
            
            # Check if this is in the fundraising/investors section (not similar projects)
            card = link.find_parent(['div', 'article', 'section'])
            if card:
                card_text = card.get_text().lower()
                # Skip if it's in "Similar Projects" section
                if 'similar' in card_text and 'project' in card_text:
                    continue
                # Also check parent containers
                parent = card.parent
                for _ in range(2):
                    if parent:
                        parent_text = parent.get_text().lower()
                        if 'similar' in parent_text and 'project' in parent_text:
                            continue
                        parent = parent.parent
            
            seen_names.add(name)
            
            investor = {
                "name": name,
                "logo": None,
                "url": href
            }
            
            # Find the card/container for this investor
            card = link.find_parent(['div', 'article', 'section'])
            if not card:
                card = link.parent
            
            # Extract logo (look for image with alt containing name or nearby)
            if card:
                images = card.find_all('img')
                for img in images:
                    src = img.get('src', '')
                    alt = img.get('alt', '')
                    
                    # Skip default logos
                    if 'default_logo' in src:
                        continue
                    
                    # This might be an investor logo
                    if src and (name.lower() in alt.lower() or alt or '128x128' in src or 'jpg' in src.lower() or 'webp' in src.lower() or 'png' in src.lower()):
                        if src.startswith('/'):
                            src = urljoin(url, src)
                        if not investor["logo"]:
                            investor["logo"] = src
                            break
            
            # Only add if not already found in Method 1
            if name and name not in seen_names:
                seen_names.add(name)
                investors.append(investor)
        
        # Method 3: Extract investors from images in fundraising section (for investors without links)
        if fundraising_container:
            investor_images = fundraising_container.find_all('img', alt=True)
            for img in investor_images:
                alt = img.get('alt', '').strip()
                src = img.get('src', '')
                
                # Skip if it's a default logo, social icon, or already seen
                if (not alt or 'default' in src.lower() or 'twitter' in src.lower() or 
                    'linkedin' in src.lower() or alt in seen_names or len(alt) < 2 or len(alt) > 100):
                    continue
                
                # Check if this looks like an investor name (capitalized, reasonable length)
                # Filter out UI elements
                ui_keywords = ['fundraising', 'rounds', 'investors list', 'investor', 'button', 'icon', 'logo', 'default']
                if (alt[0].isupper() and 
                    not any(keyword in alt.lower() for keyword in ui_keywords) and
                    alt not in seen_names):
                    seen_names.add(alt)
                    investor = {
                        "name": alt,
                        "logo": src if src.startswith('http') else urljoin(url, src) if src.startswith('/') else None,
                        "url": None
                    }
                    
                    # Try to find link nearby
                    parent = img.find_parent(['div', 'article', 'section', 'a'])
                    if parent:
                        link = parent.find('a', href=True) if parent.name != 'a' else parent if parent.name == 'a' else None
                        if link and link.name == 'a':
                            href = link.get('href', '')
                            if href.startswith('/'):
                                href = urljoin(url, href)
                            investor["url"] = href
                    
                    investors.append(investor)
        
        # Method 3: Look for investor images with alt text (fallback)
        if not investors:
            investor_images = soup.find_all('img', alt=True)
            for img in investor_images:
                alt = img.get('alt', '').strip()
                src = img.get('src', '')
                
                # Skip if it's a default logo or social icon
                if not alt or 'default' in src.lower() or 'twitter' in src.lower() or 'linkedin' in src.lower():
                    continue
                
                # Check if this looks like an investor name (capitalized, reasonable length)
                if alt and len(alt) > 2 and len(alt) < 100 and alt[0].isupper() and alt not in seen_names:
                    # Check if it's in the fundraising/investors section
                    card = img.find_parent(['div', 'article', 'section'])
                    if card:
                        card_text = card.get_text().lower()
                        if 'investor' in card_text or 'fundraising' in card_text or 'core investor' in card_text:
                            seen_names.add(alt)
                            investor = {
                                "name": alt,
                                "logo": src if src.startswith('http') else urljoin(url, src) if src.startswith('/') else None,
                                "url": None
                            }
                            
                            # Try to find link
                            link = card.find('a', href=True)
                            if link:
                                href = link.get('href', '')
                                if href.startswith('/'):
                                    href = urljoin(url, href)
                                investor["url"] = href
                            
                            investors.append(investor)
        
        return investors
    
    def extract_details(self, soup):
        """Extract project details (description, tags, founded year)."""
        details = {
            "description": None,
            "tags": [],
            "founded_year": None
        }
        
        # Extract description (look for Details section)
        desc_elem = soup.find(string=re.compile(r'^Details$', re.I))
        if desc_elem:
            # Find the next sibling or parent that contains the description
            parent = desc_elem.parent
            # Look for the next paragraph or div after "Details"
            for _ in range(5):  # Check up to 5 levels
                if parent:
                    # Look for paragraph with description
                    paragraphs = parent.find_all('p')
                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        # Filter out noise (too short, contains "Index", "Score", page title, etc.)
                        if (len(text) > 50 and len(text) < 1000 and 
                            'Index' not in text and 'Score' not in text and 
                            'normalizing' not in text.lower() and 'weighting' not in text.lower() and
                            'RootData' not in text and 'Introduction' not in text and
                            'Project Introduction' not in text and
                            '\n' not in text[:100]):  # Avoid multi-line noise
                            # Clean up the text (remove extra whitespace, newlines)
                            text = ' '.join(text.split())
                            details["description"] = text
                            break
                    if details["description"]:
                        break
                    parent = parent.parent
        
        # Fallback: Look for intro text near project name (but not page title)
        if not details["description"]:
            # Look for text that contains project-related keywords but not UI elements
            all_text = soup.get_text()
            # Try to find a sentence that describes the project
            sentences = re.split(r'[.!?]\s+', all_text)
            for sentence in sentences:
                sentence = sentence.strip()
                if (len(sentence) > 50 and len(sentence) < 500 and
                    any(word in sentence.lower() for word in ['infrastructure', 'platform', 'payment', 'stablecoin', 'blockchain', 'crypto']) and
                    'Index' not in sentence and 'Score' not in sentence and
                    'RootData' not in sentence):
                    details["description"] = sentence
                    break
        
        # Extract tags
        tags_elem = soup.find(string=re.compile(r'^Tags[:]?$', re.I))
        if tags_elem:
            parent = tags_elem.parent
            while parent and parent.name != 'body':
                # Look for tag elements
                tag_links = parent.find_all('a', class_=re.compile(r'tag', re.I))
                for tag_link in tag_links:
                    tag_text = tag_link.get_text(strip=True)
                    if tag_text:
                        details["tags"].append(tag_text)
                
                # Also check for plain text tags
                text = parent.get_text()
                if 'Payment' in text or 'Infrastructure' in text:
                    # Try to extract tags from text
                    tag_pattern = re.compile(r'\b(Payment|Infrastructure|Platform|DeFi|NFT|Gaming|Web3)\b', re.I)
                    tags = tag_pattern.findall(text)
                    details["tags"].extend([t for t in tags if t not in details["tags"]])
                
                parent = parent.parent
        
        # Extract founded year
        founded_elem = soup.find(string=re.compile(r'Founded[:]?', re.I))
        if founded_elem:
            parent = founded_elem.parent
            while parent and parent.name != 'body':
                text = parent.get_text()
                year_match = re.search(r'(\d{4})', text)
                if year_match:
                    details["founded_year"] = year_match.group(1)
                    break
                parent = parent.parent
        
        return details
    
    def extract_x_follow_updates(self, soup):
        """Extract X Follow Updates."""
        updates = []
        
        updates_elem = soup.find(string=re.compile(r'Follow Updates|𝕏 Follow Updates', re.I))
        if updates_elem:
            container = updates_elem.find_parent(['div', 'section'])
            if not container:
                container = updates_elem.parent
            
            # Find all potential update items (divs, list items, etc.)
            update_items = container.find_all(['div', 'li', 'article'], recursive=True)
            
            for item in update_items:
                item_text = item.get_text()
                
                # Skip if doesn't look like an update (no date pattern)
                if not re.search(r'[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}', item_text):
                    continue
                
                update = {
                    "date": None,
                    "follower_name": None,
                    "type": None
                }
                
                # Extract date
                date_match = re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})', item_text)
                if date_match:
                    update["date"] = date_match.group(1)
                
                # Extract follower name (look for text that's not the date and not "Followers")
                # Usually appears after the date
                text_parts = item_text.split(update["date"]) if update["date"] else [item_text]
                if len(text_parts) > 1:
                    remaining_text = text_parts[1].strip()
                    # Remove "Followers" and extract name
                    remaining_text = re.sub(r'Followers?\s*', '', remaining_text, flags=re.I).strip()
                    if remaining_text and len(remaining_text) < 100:
                        update["follower_name"] = remaining_text
                
                # Extract type (e.g., "Followers")
                type_match = re.search(r'(Followers?|Following)', item_text, re.I)
                if type_match:
                    update["type"] = type_match.group(1)
                
                if update["date"] or update["follower_name"]:
                    updates.append(update)
        
        return updates
    
    def extract_x_follow_lists(self, soup):
        """Extract X Follow Lists (People, VCs, Projects)."""
        follow_lists = {
            "people": [],
            "vcs": [],
            "projects": []
        }
        
        lists_elem = soup.find(string=re.compile(r'Follow Lists|𝕏 Follow Lists', re.I))
        if lists_elem:
            container = lists_elem.find_parent(['div', 'section'])
            if not container:
                container = lists_elem.parent
            
            # Find all potential list items (cards, divs, articles)
            list_items = container.find_all(['div', 'article', 'a'], class_=re.compile(r'card|item|person|vc|project', re.I))
            
            # Also look for items with profile images
            if not list_items:
                list_items = container.find_all(['div', 'article'], recursive=True)
            
            seen_names = set()
            
            for item in list_items:
                item_text = item.get_text().strip()
                
                # Skip if too short or looks like UI element
                if len(item_text) < 3 or len(item_text) > 200:
                    continue
                
                entry = {
                    "name": None,
                    "role": None,
                    "profile_image": None
                }
                
                # Extract name (from heading, link, or image alt)
                name_elem = item.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b'])
                if name_elem:
                    entry["name"] = name_elem.get_text(strip=True)
                
                # Try from link
                if not entry["name"]:
                    link = item.find('a')
                    if link:
                        entry["name"] = link.get_text(strip=True)
                
                # Try from image alt
                if not entry["name"]:
                    img = item.find('img', alt=True)
                    if img:
                        alt = img.get('alt', '').strip()
                        if alt and len(alt) < 100:
                            entry["name"] = alt
                
                # Skip if no name or duplicate
                if not entry["name"] or entry["name"] in seen_names:
                    continue
                
                seen_names.add(entry["name"])
                
                # Extract role
                role_patterns = [
                    r'General Partner\s+at\s+\w+',
                    r'Partner\s+at\s+\w+',
                    r'Crypto researcher',
                    r'researcher',
                    r'CEO',
                    r'CTO',
                    r'Founder'
                ]
                for pattern in role_patterns:
                    role_match = re.search(pattern, item_text, re.I)
                    if role_match:
                        entry["role"] = role_match.group(0)
                        break
                
                # Extract profile image
                img = item.find('img')
                if img:
                    src = img.get('src', '')
                    if src and 'default' not in src.lower():
                        entry["profile_image"] = src
                
                # Categorize based on context and role
                item_text_lower = item_text.lower()
                if 'vc' in item_text_lower or 'venture' in item_text_lower or 'capital' in item_text_lower or 'partner' in entry.get("role", "").lower():
                    follow_lists["vcs"].append(entry)
                elif 'project' in item_text_lower or entry["name"] in [p.get("name", "") for p in follow_lists["projects"]]:
                    follow_lists["projects"].append(entry)
                else:
                    follow_lists["people"].append(entry)
        
        return follow_lists
    
    def extract_ratings(self, soup):
        """Extract ratings and reviews."""
        ratings = {
            "overall_rating": None,
            "user_count": None,
            "reviews": []
        }
        
        # Extract overall rating
        rating_elem = soup.find(string=re.compile(r'Rate\s+\w+|Rated by', re.I))
        if rating_elem:
            parent = rating_elem.parent
            while parent and parent.name != 'body':
                text = parent.get_text()
                # Look for rating like "7.5" or "★★★★ 7.5"
                rating_match = re.search(r'(\d+\.?\d*)', text)
                if rating_match:
                    ratings["overall_rating"] = float(rating_match.group(1))
                
                # Extract user count
                user_match = re.search(r'Rated by\s+([\d,]+)\s+users?', text, re.I)
                if user_match:
                    ratings["user_count"] = int(user_match.group(1).replace(',', ''))
                
                parent = parent.parent
        
        # Extract reviews
        reviews_section = soup.find(string=re.compile(r'Hottest|Reviews', re.I))
        if reviews_section:
            container = reviews_section.find_parent(['div', 'section'])
            if not container:
                container = reviews_section.parent
            
            review_items = container.find_all(['div', 'article'], class_=re.compile(r'review|comment|item', re.I))
            
            for item in review_items:
                review = {
                    "author": None,
                    "rating": None,
                    "date": None,
                    "text": None
                }
                
                # Extract author
                author_elem = item.find(['a', 'strong', 'b'], class_=re.compile(r'author|user|name', re.I))
                if author_elem:
                    review["author"] = author_elem.get_text(strip=True)
                
                # Extract rating (stars)
                stars = item.find_all(string=re.compile(r'★|⭐', re.I))
                if stars:
                    review["rating"] = len([s for s in stars if '★' in s or '⭐' in s])
                
                # Extract date
                date_match = re.search(r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})', item.get_text())
                if date_match:
                    review["date"] = date_match.group(1)
                
                # Extract review text
                text_elem = item.find('p') or item.find('div', class_=re.compile(r'text|content', re.I))
                if text_elem:
                    review["text"] = text_elem.get_text(strip=True)
                
                if review["author"] or review["text"]:
                    ratings["reviews"].append(review)
        
        return ratings
    
    def extract_similar_projects(self, soup, url):
        """Extract similar projects."""
        similar_projects = []
        
        similar_elem = soup.find(string=re.compile(r'Similar Projects', re.I))
        if similar_elem:
            container = similar_elem.find_parent(['div', 'section'])
            if not container:
                container = similar_elem.parent
            
            project_items = container.find_all(['div', 'a', 'article'], class_=re.compile(r'project|card|item', re.I))
            
            for item in project_items:
                project = {
                    "name": None,
                    "description": None,
                    "url": None
                }
                
                # Extract name
                name_elem = item.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b'])
                if name_elem:
                    project["name"] = name_elem.get_text(strip=True)
                
                # Extract description
                desc_elem = item.find('p') or item.find('div', class_=re.compile(r'description|desc', re.I))
                if desc_elem:
                    project["description"] = desc_elem.get_text(strip=True)
                
                # Extract URL
                link = item.find('a', href=True) if item.name != 'a' else item
                if link and link.name == 'a':
                    href = link.get('href', '')
                    if href:
                        if href.startswith('/'):
                            href = urljoin(url, href)
                        project["url"] = href
                
                if project["name"]:
                    similar_projects.append(project)
        
        return similar_projects
    
    def scrape(self, url):
        """Main scraping method."""
        print(f"🌐 Fetching: {url}")
        
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            time.sleep(2)
            
            # Wait for Cloudflare if present
            if 'cloudflare' in self.page.content().lower() or 'just a moment' in self.page.content().lower():
                print("⏳ Waiting for Cloudflare challenge...")
                self.wait_for_cloudflare()
            
            # Scroll to load content
            print("📜 Scrolling to load content...")
            self.scroll_to_load()
            
            # Get page content
            html = self.page.content()
            soup = BeautifulSoup(html, 'lxml')
            
            print("📊 Extracting data...")
            
            # Extract all data
            project_info = self.extract_project_basic_info(soup, url)
            team = self.extract_team_members(soup, url)
            investors = self.extract_investors(soup, url)
            
            # Get team member names to filter them out from investors
            team_names = {member.get("name", "").lower() for member in team if member.get("name")}
            
            # Filter out the project itself, team members, and UI elements from investors
            project_name = project_info.get("name", "").lower()
            ui_keywords = ['fundraising', 'rounds', 'investors list', 'button', 'icon', 'download', 
                          'all projects', 'market', 'people', 'x data', 'transparency', 'upcoming', 
                          'token', 'new', 'rootdata', 'news', 'dashboard', 'archives', 'deals']
            investors = [
                inv for inv in investors 
                if inv.get("name", "").lower() != project_name and
                inv.get("name", "").lower() not in team_names and
                not any(keyword in inv.get("name", "").lower() for keyword in ui_keywords) and
                len(inv.get("name", "")) > 2 and len(inv.get("name", "")) < 100
            ]
            
            # If we still don't have the expected investors, try extracting from text patterns
            # Look for common investor name patterns in the fundraising section
            if len(investors) < 2:
                seen_names = {inv.get("name", "").lower() for inv in investors}
                fundraising_elem = soup.find(string=re.compile(r'Fundraising', re.I))
                if fundraising_elem:
                    parent = fundraising_elem.parent
                    for _ in range(5):
                        if parent:
                            text = parent.get_text()
                            # Look for patterns like "Paxos", "Connecticut Innovations", etc.
                            # These are usually company names with capital letters
                            investor_pattern = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:Ventures|Capital|Partners|Innovations|Group))?)\b')
                            matches = investor_pattern.findall(text)
                            for match in matches:
                                name = match.strip()
                                # Skip if it's a common word or UI element
                                if (name.lower() not in seen_names and len(name) > 2 and len(name) < 100 and
                                    not any(ui in name.lower() for ui in ui_keywords + [project_name])):
                                    seen_names.add(name.lower())
                                    investors.append({
                                        "name": name,
                                        "logo": None,
                                        "url": None
                                    })
                            if len(investors) >= 4:  # Found enough investors
                                break
                            parent = parent.parent
            
            data = {
                "scraped_at": datetime.now().isoformat(),
                "url": url,
                "page_title": self.page.title(),
                "project_basic_info": project_info,
                "metrics": self.extract_metrics(soup),
                "comparison_data": self.extract_comparison_data(soup),
                "team": team,
                "investors": investors,
                "details": self.extract_details(soup),
                "x_follow_updates": self.extract_x_follow_updates(soup),
                "x_follow_lists": self.extract_x_follow_lists(soup),
                "ratings": self.extract_ratings(soup),
                "similar_projects": self.extract_similar_projects(soup, url)
            }
            
            return data
            
        except PlaywrightTimeout:
            print("❌ Error: Page load timeout")
            return None
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return None


def save_csv(data, output_file):
    """Save data to CSV file."""
    output_path = Path(os.getcwd()) / output_file
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        
        # Write header
        writer.writerow(['Section', 'Field', 'Value'])
        
        # Project Basic Info
        basic_info = data.get('project_basic_info', {})
        writer.writerow(['Project Basic Info', 'Name', basic_info.get('name', '')])
        writer.writerow(['Project Basic Info', 'Tagline', basic_info.get('tagline', '')])
        writer.writerow(['Project Basic Info', 'Transparency Score', basic_info.get('transparency_score', '')])
        writer.writerow(['Project Basic Info', 'Website', basic_info.get('website', '')])
        writer.writerow(['Project Basic Info', 'X Handle', basic_info.get('x_handle', '')])
        writer.writerow(['Project Basic Info', 'X URL', basic_info.get('x_url', '')])
        
        # Metrics
        metrics = data.get('metrics', {})
        writer.writerow(['Metrics', 'RD Popularity Index', metrics.get('rd_popularity_index', '')])
        writer.writerow(['Metrics', 'RD Popularity Change', metrics.get('rd_popularity_change', '')])
        writer.writerow(['Metrics', 'RD Popularity Rank', metrics.get('rd_popularity_rank', '')])
        writer.writerow(['Metrics', 'RD Growth Index', metrics.get('rd_growth_index', '')])
        writer.writerow(['Metrics', 'RD Growth Change', metrics.get('rd_growth_change', '')])
        writer.writerow(['Metrics', 'RD Growth Rank', metrics.get('rd_growth_rank', '')])
        
        # Comparison Data
        comparison = data.get('comparison_data', {})
        writer.writerow(['Comparison Data', 'Total Raised', comparison.get('total_raised', '')])
        writer.writerow(['Comparison Data', 'Total Raised Rank', comparison.get('total_raised_rank', '')])
        writer.writerow(['Comparison Data', 'FDV', comparison.get('fdv', '')])
        writer.writerow(['Comparison Data', '24h Trading Volume', comparison.get('trading_volume_24h', '')])
        
        # Team Members
        team = data.get('team', [])
        for i, member in enumerate(team, 1):
            writer.writerow(['Team', f'Member {i} - Name', member.get('name', '')])
            writer.writerow(['Team', f'Member {i} - Role', member.get('role', '')])
            writer.writerow(['Team', f'Member {i} - X URL', member.get('x_url', '')])
            writer.writerow(['Team', f'Member {i} - LinkedIn URL', member.get('linkedin_url', '')])
            writer.writerow(['Team', f'Member {i} - Profile Image', member.get('profile_image', '')])
            writer.writerow(['Team', f'Member {i} - Profile URL', member.get('profile_url', '')])
        
        # Investors
        investors = data.get('investors', [])
        for i, investor in enumerate(investors, 1):
            writer.writerow(['Investors', f'Investor {i} - Name', investor.get('name', '')])
            writer.writerow(['Investors', f'Investor {i} - Logo', investor.get('logo', '')])
            writer.writerow(['Investors', f'Investor {i} - URL', investor.get('url', '')])
        
        # Details
        details = data.get('details', {})
        writer.writerow(['Details', 'Description', details.get('description', '')])
        writer.writerow(['Details', 'Tags', ', '.join(details.get('tags', []))])
        writer.writerow(['Details', 'Founded Year', details.get('founded_year', '')])
        
        # X Follow Updates
        updates = data.get('x_follow_updates', [])
        for i, update in enumerate(updates, 1):
            writer.writerow(['X Follow Updates', f'Update {i} - Date', update.get('date', '')])
            writer.writerow(['X Follow Updates', f'Update {i} - Follower Name', update.get('follower_name', '')])
            writer.writerow(['X Follow Updates', f'Update {i} - Type', update.get('type', '')])
        
        # X Follow Lists - People
        follow_lists = data.get('x_follow_lists', {})
        people = follow_lists.get('people', [])
        for i, person in enumerate(people, 1):
            writer.writerow(['X Follow Lists - People', f'Person {i} - Name', person.get('name', '')])
            writer.writerow(['X Follow Lists - People', f'Person {i} - Role', person.get('role', '')])
            writer.writerow(['X Follow Lists - People', f'Person {i} - Profile Image', person.get('profile_image', '')])
        
        # X Follow Lists - VCs
        vcs = follow_lists.get('vcs', [])
        for i, vc in enumerate(vcs, 1):
            writer.writerow(['X Follow Lists - VCs', f'VC {i} - Name', vc.get('name', '')])
            writer.writerow(['X Follow Lists - VCs', f'VC {i} - Role', vc.get('role', '')])
            writer.writerow(['X Follow Lists - VCs', f'VC {i} - Profile Image', vc.get('profile_image', '')])
        
        # X Follow Lists - Projects
        projects_list = follow_lists.get('projects', [])
        for i, proj in enumerate(projects_list, 1):
            writer.writerow(['X Follow Lists - Projects', f'Project {i} - Name', proj.get('name', '')])
            writer.writerow(['X Follow Lists - Projects', f'Project {i} - Role', proj.get('role', '')])
            writer.writerow(['X Follow Lists - Projects', f'Project {i} - Profile Image', proj.get('profile_image', '')])
        
        # Ratings
        ratings = data.get('ratings', {})
        writer.writerow(['Ratings', 'Overall Rating', ratings.get('overall_rating', '')])
        writer.writerow(['Ratings', 'User Count', ratings.get('user_count', '')])
        reviews = ratings.get('reviews', [])
        for i, review in enumerate(reviews, 1):
            writer.writerow(['Ratings - Reviews', f'Review {i} - Author', review.get('author', '')])
            writer.writerow(['Ratings - Reviews', f'Review {i} - Rating', review.get('rating', '')])
            writer.writerow(['Ratings - Reviews', f'Review {i} - Date', review.get('date', '')])
            writer.writerow(['Ratings - Reviews', f'Review {i} - Text', review.get('text', '')])
        
        # Similar Projects
        similar = data.get('similar_projects', [])
        for i, proj in enumerate(similar, 1):
            writer.writerow(['Similar Projects', f'Project {i} - Name', proj.get('name', '')])
            writer.writerow(['Similar Projects', f'Project {i} - Description', proj.get('description', '')])
            writer.writerow(['Similar Projects', f'Project {i} - URL', proj.get('url', '')])
        
        # Metadata
        writer.writerow(['Metadata', 'Scraped At', data.get('scraped_at', '')])
        writer.writerow(['Metadata', 'URL', data.get('url', '')])
        writer.writerow(['Metadata', 'Page Title', data.get('page_title', '')])
    
    print(f"✅ CSV saved: {output_path.name}")


def main():
    parser = argparse.ArgumentParser(description='Project Detail Scraper')
    parser.add_argument('url', help='URL of the project detail page')
    parser.add_argument('-o', '--output', help='Output CSV filename')
    parser.add_argument('--no-headless', action='store_true', help='Run browser in visible mode')
    parser.add_argument('--timeout', type=int, default=60000, help='Page load timeout in ms')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🔍 Project Detail Scraper")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🌐 URL: {args.url}")
    
    # Generate output filename
    output_filename = args.output
    if not output_filename:
        from urllib.parse import urlparse
        path = urlparse(args.url).path.strip('/')
        # Extract project name from path
        project_name = path.split('/')[-1] if '/' in path else 'project'
        output_filename = f"project_{project_name}.csv"
    
    print(f"📁 Output: {output_filename}")
    print("=" * 60)
    print()
    
    with ProjectScraper(headless=not args.no_headless, timeout=args.timeout) as scraper:
        data = scraper.scrape(args.url)
        
        if data:
            save_csv(data, output_filename)
            
            print()
            print("=" * 60)
            print("📊 EXTRACTION SUMMARY")
            print("=" * 60)
            print(f"Project: {data.get('project_basic_info', {}).get('name', 'N/A')}")
            print(f"Team Members: {len(data.get('team', []))}")
            print(f"Investors: {len(data.get('investors', []))}")
            print(f"X Follow Updates: {len(data.get('x_follow_updates', []))}")
            print(f"Similar Projects: {len(data.get('similar_projects', []))}")
            print("=" * 60)
        else:
            print("❌ Scraping failed")
            print()
            print("💡 Tips:")
            print("   - Try running with --no-headless to see what's happening")
            print("   - Increase timeout: --timeout 120000")
            print("   - Check your internet connection")
            sys.exit(1)


if __name__ == '__main__':
    main()

