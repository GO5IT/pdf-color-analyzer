# PDF Color Analyzer

A Python tool for analyzing colours in PDF files. It extracts all colours used within vector content inside and outside of the document's boundaries (media box), including their positions, opacity values, and whether they're used in text or shapes.

Note: it won't get colours from inside embedded raster images - only from text and shapes / paths.

We made this as part of an automated system to check whether a PDF is suitable for use in creating a brass 
stamp - we needed to ensure that files only contain (0,0,0,0) or (0,0,0,100) CMYK values at 100% opacity.


## Features

- Identifies CMYK, RGB and Grayscale colours
- Tracks opacity values, including nested opacities (e.g. a 50% opacity object inside a 70% opacity frame = 35%)
- Identifies text and shape elements
- Extracts text content of text elements
- Reports positions in millimeters
- Determines whether colours are inside the visible bounds
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
- `colors_in_bounds`: A list of unique colours found within the document boundaries
- `pages`: Detailed colour information for each page, including positions and contexts

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

## How should colour be spelt?

- 'Color' inside code
- 'Colour' in documentation. 
Apologies if this is confusing, we're British but try to write the code using international English.

## License

MIT License

Copyright (c) 2024 Dan Barker / Yearbook Machine Limited

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
