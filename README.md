# PDF Color Analyzer

A Python tool for analyzing colors in PDF files. It extracts all colors used within the document's boundaries, including their positions, opacity values, and whether they're used in text or shapes.

## Features

- Extracts both CMYK and RGB colors
- Reports color positions in millimeters
- Identifies text vs shape elements
- Tracks opacity values
- Distinguishes between in-bounds and out-of-bounds colors
- Outputs results in structured JSON format

## Requirements

- Python 3.6+
- pikepdf library

## Installation

Option 1: Using the install script (Unix/MacOS):
```bash
chmod +x install.sh
./install.sh
```

Option 2: Manual installation:
1. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows, use: venv\Scripts\activate
   ```

2. Install required packages:
   ```bash
   pip install pikepdf
   ```

## Usage

Basic usage:
```bash
python3 pdf_color_analyzer.py input.pdf
```

Enable debug output:
```bash
python3 pdf_color_analyzer.py input.pdf --debug
```

## Output Format

The tool outputs JSON with two main sections:
- `colors_in_bounds`: A list of unique colors found within the document boundaries
- `pages`: Detailed color information for each page, including positions and contexts

Example output:
```json
{
  "colors_in_bounds": [
    {
      "colorspace": "CMYK",
      "value": [0, 0, 0, 100],
      "opacity": 100
    }
  ],
  "pages": {
    "1": {
      "colors": [
        {
          "colorspace": "CMYK",
          "value": [0, 0, 0, 100],
          "opacity": 100,
          "out_of_bounds": false,
          "bounds": {
            "x": 10.0,
            "y": 10.0,
            "width": 50.0,
            "height": 20.0,
            "type": "rectangle"
          }
        }
      ]
    }
  }
}
```

## Notes

- All position measurements are in millimeters
- CMYK values are in percentages (0-100)
- RGB values are in standard format (0-255)
- Opacity values are in percentages (0-100)
