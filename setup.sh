#!/bin/bash
# Setup script for CryptoRank Universal Scraper

echo "🚀 Setting up CryptoRank Universal Scraper..."
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8+ first."
    exit 1
fi

echo "✅ Python found: $(python3 --version)"
echo ""

# Install dependencies
echo "📦 Installing Python dependencies..."
pip3 install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "❌ Failed to install dependencies"
    exit 1
fi

echo ""
echo "🌐 Installing Playwright browser..."
python3 -m playwright install chromium

if [ $? -ne 0 ]; then
    echo "❌ Failed to install Playwright browser"
    exit 1
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "📖 Usage:"
echo "   python3 scraper.py <url>"
echo ""
echo "📝 Examples:"
echo "   python3 scraper.py https://cryptorank.io/price/kucoin-shares"
echo "   python3 scraper.py https://cryptorank.io/incubators/y-combinator --csv"
echo ""


