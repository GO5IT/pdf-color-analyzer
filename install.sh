#!/bin/bash

# PDF Color Analyzer - Install/Reinstall Script
# This script sets up the virtual environment and installs dependencies
# Use --force to reinstall even if virtual environment exists

set -e  # Exit on any error

FORCE_REINSTALL=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --force|-f)
            FORCE_REINSTALL=true
            shift
            ;;
        --help|-h)
            echo "PDF Color Analyzer - Install Script"
            echo "Usage: $0 [--force]"
            echo "  --force, -f    Force reinstall (remove existing virtual environment)"
            echo "  --help, -h     Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "🔧 PDF Color Analyzer - Install Script"
echo "====================================="
echo

# Check if we're in the right directory
if [ ! -f "pdf_color_analyzer.py" ]; then
    echo "❌ Error: pdf_color_analyzer.py not found in current directory"
    echo "Please run this script from the pdf-color-analyzer directory"
    exit 1
fi

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is required but not installed."
    echo "Please install Python 3 and try again."
    exit 1
fi

# Handle existing virtual environment
if [ -d "venv" ]; then
    if [ "$FORCE_REINSTALL" = true ]; then
        echo "🗑️  Removing existing virtual environment (--force specified)..."
        # Deactivate if active
        if [ -n "$VIRTUAL_ENV" ]; then
            deactivate 2>/dev/null || true
        fi
        rm -rf venv
    else
        echo "📁 Virtual environment already exists"
        echo "   Use --force to reinstall, or activate with: source venv/bin/activate"
        
        # Test if existing installation works
        echo "🧪 Testing existing installation..."
        source venv/bin/activate
        if python3 -c "from pikepdf import Pdf, Object; print('✅ Existing installation works!')" 2>/dev/null; then
            echo "🎉 Ready to use! Run: python3 pdf_color_analyzer.py your_file.pdf"
            exit 0
        else
            echo "⚠️  Existing installation appears broken. Reinstalling..."
            deactivate 2>/dev/null || true
            rm -rf venv
        fi
    fi
fi

# Create new virtual environment
echo "🏗️  Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# Test installation
echo "🧪 Testing installation..."
if python3 -c "from pikepdf import Pdf, Object; print('✅ pikepdf imported successfully')" 2>/dev/null; then
    echo "✅ Installation successful!"
    echo
    echo "🎉 Ready to use! You can now run:"
    echo "   source venv/bin/activate"
    echo "   python3 pdf_color_analyzer.py your_file.pdf"
else
    echo "❌ Installation test failed"
    exit 1
fi 