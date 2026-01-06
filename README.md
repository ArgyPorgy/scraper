# Project Scraper

A specialized scraper for project detail pages on RootData/CryptoRank.

## Quick Setup

```bash
# Install dependencies
pip install playwright beautifulsoup4 lxml

# Install browser
python -m playwright install chromium
```

## Usage

### Project Detail Scraper (Recommended)

```bash
# Scrape a project page
##example: 

python project_scraper.py " "

python project_scraper.py "https://www.rootdata.com/Projects/detail/Coinbax"

# With custom output file
python project_scraper.py "https://www.rootdata.com/Projects/detail/Coinbax" -o my_output.csv

```

### Generic Scraper

```bash
# Scrape any page
python scraper.py "https://www.rootdata.com/Fundraising"

# Export as CSV too
python scraper.py "https://www.rootdata.com/Fundraising" --csv
```

## Output

All output files are saved in your current working directory as CSV files.

## Note

Always quote URLs in the terminal to avoid shell expansion issues:
```bash
# ✅ Correct
python project_scraper.py "https://www.rootdata.com/Projects/detail/Coinbax?k=MjMwMTk%3D"

# ❌ Wrong (zsh will try to match files)
python project_scraper.py https://www.rootdata.com/Projects/detail/Coinbax?k=MjMwMTk%3D
```
