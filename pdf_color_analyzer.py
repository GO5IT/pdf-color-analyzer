import sys
from pikepdf import Pdf, Object
import re
from collections import defaultdict
import json
import argparse

DEBUG = False

# At the top of the file, add these globals
_processed_streams = set()
_processed_xobjects = {}  # Dictionary to track XObjects per page

def pt_to_mm(pt):
    """Convert points to millimeters"""
    return round(float(pt) * 0.352778, 1)

def get_color_spaces_from_resources(resources):
    """Extract color space definitions from PDF resources"""
    cs_dict = {}
    
    # First check for Group CS
    if resources and '/Group' in resources:
        group = resources['/Group']
        if '/CS' in group:
            group_cs = str(group['/CS'])
            if DEBUG:
                print(f"Found Group color space: {group_cs}")
            if group_cs == '/DeviceCMYK':
                cs_dict['__default__'] = 'CMYK'
            elif group_cs == '/DeviceRGB':
                cs_dict['__default__'] = 'RGB'
            elif group_cs == '/DeviceGray':
                cs_dict['__default__'] = 'Gray'
    
    if resources:
        if DEBUG:
            print("\nResource keys available:", resources.keys())
            print("\nDetailed Resource Contents:")
            for key in resources.keys():
                print(f"\n{key}:")
                try:
                    if isinstance(resources[key], dict):
                        for subkey, value in resources[key].items():
                            print(f"  {subkey}: {value}")
                            # If it's a nested dictionary, go deeper
                            if isinstance(value, dict):
                                for k, v in value.items():
                                    print(f"    {k}: {v}")
                    else:
                        print(f"  {resources[key]}")
                except Exception as e:
                    print(f"  Error accessing resource: {e}")
        
        if '/ColorSpace' in resources:
            # if DEBUG:
            #     print("\nColorSpace entries:", resources['/ColorSpace'].keys())
            #     print("ColorSpace values:")
            #     for name, value in resources['/ColorSpace'].items():
            #         print(f"  {name}: {value}")
            
            for cs_name, cs_value in resources['/ColorSpace'].items():
                # if DEBUG:
                #     print(f"\nProcessing color space {cs_name}: {cs_value}")
                #     print(f"Type: {type(cs_value)}")
                
                try:
                    # Convert pikepdf.Object to list if possible
                    if isinstance(cs_value, (list, tuple)):
                        array_items = cs_value
                    else:
                        # Try to convert pikepdf.Object to list
                        try:
                            array_items = list(cs_value)
                        except Exception as e:
                            if DEBUG:
                                print(f"Could not convert to list: {e}")
                            continue
                    
                    base_cs = str(array_items[0])
                    if DEBUG:
                        print(f"Processing array-based color space with base: {base_cs}")
                    
                    if base_cs == '/DeviceCMYK':
                        cs_dict[cs_name] = 'CMYK'
                    elif base_cs == '/DeviceRGB':
                        cs_dict[cs_name] = 'RGB'
                    elif base_cs == '/DeviceGray':
                        cs_dict[cs_name] = 'Gray'
                    elif base_cs == '/ICCBased':
                        # Get the stream object which is the second element
                        icc_stream = array_items[1]
                        try:
                            n_components = int(icc_stream.N)
                            if DEBUG:
                                print(f"ICC profile with {n_components} components")
                            if n_components == 4:
                                cs_dict[cs_name] = 'CMYK'
                            elif n_components == 3:
                                cs_dict[cs_name] = 'RGB'
                            elif n_components == 1:
                                cs_dict[cs_name] = 'Gray'
                        except Exception as e:
                            if DEBUG:
                                print(f"Error getting ICC components: {e}")
                    elif base_cs == '/DeviceN':
                        if len(array_items) >= 3:
                            alternate_cs = str(array_items[2])
                            if DEBUG:
                                print(f"DeviceN with alternate color space: {alternate_cs}")
                            if alternate_cs == '/DeviceCMYK':
                                cs_dict[cs_name] = 'CMYK'
                            elif alternate_cs == '/DeviceRGB':
                                cs_dict[cs_name] = 'RGB'
                            elif alternate_cs == '/DeviceGray':
                                cs_dict[cs_name] = 'Gray'
                            
                            # Also check Process dictionary if available
                            if len(array_items) >= 5:
                                process_dict = array_items[4]
                                if hasattr(process_dict, 'get'):
                                    process = process_dict.get('/Process')
                                    if process and '/ColorSpace' in process:
                                        process_cs = str(process['/ColorSpace'])
                                        if DEBUG:
                                            print(f"DeviceN process color space: {process_cs}")
                                        if process_cs == '/DeviceCMYK':
                                            cs_dict[cs_name] = 'CMYK'
                                        elif process_cs == '/DeviceRGB':
                                            cs_dict[cs_name] = 'RGB'
                                        elif process_cs == '/DeviceGray':
                                            cs_dict[cs_name] = 'Gray'
                    else:
                        if DEBUG:
                            print(f"Warning: Unknown color space base: {base_cs}")
                except Exception as e:
                    if DEBUG:
                        print(f"Error processing color space {cs_name}: {str(e)}")
                        import traceback
                        traceback.print_exc()
                    continue
    
    if DEBUG:
        print(f"\nExtracted color spaces from resources: {cs_dict}")
    return cs_dict

class PDFOperationParser:
    def __init__(self, color_spaces=None):
        self.stack = []
        self.current_color = None
        self.color_space = None
        self.operations = []
        self.is_clipping = False
        self.is_container = False
        self.path_in_progress = False
        self.graphics_state = None
        self.current_rect = None
        self.color_spaces = color_spaces or {}
        self.in_text_block = False
        self.current_text_position = None
        self.current_text_content = None
        
        if '__default__' in self.color_spaces:
            self.color_space = self.color_spaces['__default__']
            if DEBUG:
                print(f"Using default color space: {self.color_space}")
    
    def parse_operations(self, content):
        # Modified regex to capture all token types while keeping parenthetical content together
        tokens = re.findall(rb'''
            \([^)]*\)          # Text content in parentheses
            |/\w+              # Names starting with /
            |[-+]?\d*\.?\d+   # Numbers (integer or float)
            |[A-Za-z]+         # Operators
            |[\[\]{}()]        # Other special characters
            |\s+               # Whitespace
        ''', content, re.VERBOSE)
        
        if DEBUG:
            print("\nAll tokens:")
            print(tokens)
        
        i = 0
        while i < len(tokens):
            token = tokens[i].strip()
            if not token:
                i += 1
                continue
            
            # When processing text arrays
            if token == b'[':
                if DEBUG:
                    print("Starting text array processing")
                text_parts = []
                i += 1
                
                while i < len(tokens) and tokens[i].strip() != b']':
                    current_token = tokens[i].strip()
                    
                    # Check if this is a text string (starts with parenthesis)
                    if current_token.startswith(b'(') and current_token.endswith(b')'):
                        # Remove the parentheses and decode
                        text = current_token[1:-1].decode('utf-8', errors='replace')
                        if DEBUG:
                            print(f"Found text part: {text}")
                        text_parts.append(text)
                    
                    i += 1
                
                # Join text parts without extra spaces
                self.current_text_content = ''.join(text_parts).replace('  ', ' ').strip()
                if DEBUG:
                    print(f"Assembled text content: {self.current_text_content}")
            
            # When adding text operations
            elif token in [b'Tj', b'TJ']:
                if DEBUG:
                    print(f"Text operation with {self.color_space} color {self.current_color}")
                    print(f"Text position: {self.current_text_position}")
                    print(f"Text content: {self.current_text_content}")
                
                operation = {
                    'type': 'text',
                    'color': self.current_color,
                    'color_space': self.color_space,
                    'graphics_state': self.graphics_state,
                    'current_rect': self.current_text_position,
                    'text_position': self.current_text_position,
                    'text_content': self.current_text_content
                }
                self.operations.append(operation)
                if DEBUG:
                    print(f"Added operation with text: {self.current_text_content}")
                self.current_text_content = None  # Reset text content
            
            # Track text blocks
            elif token == b'BT':
                self.in_text_block = True
                if DEBUG:
                    print("Entering text block")
            elif token == b'ET':
                self.in_text_block = False
                if DEBUG:
                    print("Exiting text block")
            
            # For color operations without explicit color space
            if token == b'k' or token == b'K':  # CMYK operations
                if self.color_space is None:
                    self.color_space = 'CMYK'
                    if DEBUG:
                        print("Implicitly using CMYK color space for k/K operation")
            elif token == b'g' or token == b'G':  # Gray operations
                if self.color_space is None:
                    self.color_space = 'Gray'
                    if DEBUG:
                        print("Implicitly using Gray color space for g/G operation")
            
            # For any color operation, warn if we don't have a color space but continue processing
            if token in [b'scn', b'SCN', b'sc', b'SC', b'rg', b'RG', b'k', b'K', b'g', b'G']:
                if self.color_space is None:
                    if DEBUG:
                        print(f"Warning: Color operation {token} encountered but no color space has been set")
                    # Skip this color operation
                    if token in [b'k', b'K']:  # CMYK operations
                        if len(self.stack) >= 4:
                            self.stack = self.stack[:-4]  # Remove the color values from stack
                    elif token in [b'rg', b'RG']:  # RGB operations
                        if len(self.stack) >= 3:
                            self.stack = self.stack[:-3]
                    elif token in [b'g', b'G']:  # Gray operations
                        if len(self.stack) >= 1:
                            self.stack = self.stack[:-1]
                    i += 1
                    continue
            
            # Handle RGB values set via scn after CS0
            if token == b'scn' and self.color_space == 'RGB':
                if len(self.stack) >= 3:
                    b = self.stack.pop()
                    g = self.stack.pop()
                    r = self.stack.pop()
                    self.current_color = (r, g, b)
                    if DEBUG:
                        rgb_255 = tuple(round(c * 255) for c in self.current_color)
                        print(f"RGB color via scn: {rgb_255}")
                i += 1
                continue
            
            if token == b'scn' and self.color_space == 'CMYK':  # Color values in CMYK space
                if len(self.stack) >= 1:  # For the case of '1 scn'
                    value = self.stack.pop()
                    if value == 1:
                        self.current_color = (0, 0, 0, 1)
                        if DEBUG:
                            print(f"Single value scn interpreted as black: {self.current_color}")
                elif len(self.stack) >= 4:
                    k = self.stack.pop()
                    y = self.stack.pop()
                    m = self.stack.pop()
                    c = self.stack.pop()
                    self.current_color = (c, m, y, k)
                    if DEBUG:
                        print(f"CMYK color space color: {self.current_color}")
                i += 1
                continue
            
            if token.startswith(b'/CS') or token.startswith(b'/Device'):
                color_space_name = token.decode('utf-8', 'ignore')
                i += 1
                # Skip whitespace
                while i < len(tokens) and tokens[i].strip() == b'':
                    i += 1
                if i < len(tokens) and tokens[i].strip() in [b'cs', b'CS']:
                    # Direct device color space
                    if color_space_name == '/DeviceRGB':
                        self.color_space = 'RGB'
                    elif color_space_name == '/DeviceCMYK':
                        self.color_space = 'CMYK'
                    elif color_space_name == '/DeviceGray':
                        self.color_space = 'Gray'
                    # Named color space lookup
                    elif color_space_name in self.color_spaces:
                        self.color_space = self.color_spaces[color_space_name]
                    else:
                        raise ValueError(f"Unknown color space: {color_space_name}. Available color spaces: {self.color_spaces}")
                    
                    if DEBUG:
                        print(f"Color space changed to: {self.color_space} for {color_space_name}")
                    i += 1
                    continue
            
            # For any color operation, ensure we have a color space
            if token in [b'scn', b'SCN', b'sc', b'SC', b'rg', b'RG', b'k', b'K', b'g', b'G']:
                if self.color_space is None:
                    raise ValueError(f"Color operation {token} encountered but no color space has been set")
            
            if token == b'rg' or token == b'RG':  # RGB color
                if len(self.stack) >= 3:
                    b = self.stack.pop()
                    g = self.stack.pop()
                    r = self.stack.pop()
                    self.current_color = (r, g, b)
                    self.color_space = 'RGB'
                    if DEBUG:
                        print(f"RGB color: {tuple(round(c * 100) for c in self.current_color)}")
                i += 1
                continue
            
            if re.match(rb'[+-]?(?:\d*\.\d+|\d+\.?)', token):
                self.stack.append(float(token))
                i += 1
                continue
            
            if token.startswith(b'/GS'):
                self.graphics_state = token
                i += 2
                continue
            
            # When processing rectangle operations
            elif token == b're':
                # Get the rectangle parameters
                if len(self.stack) >= 4:
                    h = float(self.stack.pop())
                    w = float(self.stack.pop())
                    y = float(self.stack.pop())
                    x = float(self.stack.pop())
                    self.current_rect = (x, y, w, h)  # Store the current rectangle
                    if DEBUG:
                        print(f"Rectangle: {pt_to_mm(x)}mm, {pt_to_mm(y)}mm, {pt_to_mm(w)}mm x {pt_to_mm(h)}mm")
                        print(f"Storing rectangle: {self.current_rect}")
            
            elif token == b'k':  # CMYK color
                if len(self.stack) >= 4:
                    k = self.stack.pop()
                    y = self.stack.pop()
                    m = self.stack.pop()
                    c = self.stack.pop()
                    self.current_color = (c, m, y, k)
                    self.color_space = 'CMYK'
                    if DEBUG:
                        print(f"CMYK color: {self.current_color}")
            
            elif token == b'K':  # CMYK color for stroke
                if len(self.stack) >= 4:
                    k = self.stack.pop()
                    y = self.stack.pop()
                    m = self.stack.pop()
                    c = self.stack.pop()
                    self.current_color = (c, m, y, k)
                    self.color_space = 'CMYK'
                    if DEBUG:
                        print(f"CMYK stroke color: {self.current_color}")
            
            elif token == b'g':  # Grayscale
                if len(self.stack) >= 1:
                    gray = self.stack.pop()
                    self.current_color = (0, 0, 0, 1-gray)  # Convert to CMYK
                    self.color_space = 'CMYK'
                    if DEBUG:
                        print(f"Grayscale {gray} converted to CMYK: {self.current_color}")
            
            elif token == b'G':  # Grayscale for stroke
                if len(self.stack) >= 1:
                    gray = self.stack.pop()
                    self.current_color = (0, 0, 0, 1-gray)  # Convert to CMYK
                    self.color_space = 'CMYK'
                    if DEBUG:
                        print(f"Grayscale stroke {gray} converted to CMYK: {self.current_color}")
            
            elif token == b'sc' or token == b'scn':  # Color in current color space
                if len(self.stack) >= 4 and self.color_space == 'CMYK':
                    k = self.stack.pop()
                    y = self.stack.pop()
                    m = self.stack.pop()
                    c = self.stack.pop()
                    self.current_color = (c, m, y, k)
                    if DEBUG:
                        print(f"Color space color: {self.current_color}")
            
            elif token == b'SC' or token == b'SCN':  # Stroke color in current color space
                if len(self.stack) >= 4 and self.color_space == 'CMYK':
                    k = self.stack.pop()
                    y = self.stack.pop()
                    m = self.stack.pop()
                    c = self.stack.pop()
                    self.current_color = (c, m, y, k)
                    if DEBUG:
                        print(f"Color space stroke color: {self.current_color}")
            
            # Track text matrix position (Tm operator)
            if token == b'Tm':
                if len(self.stack) >= 6:
                    # Tm takes 6 numbers: a b c d e f
                    # where e and f are the x,y position
                    f = self.stack.pop()  # y position
                    e = self.stack.pop()  # x position
                    self.stack = self.stack[:-4]  # Remove a b c d
                    self.current_text_position = (e, f)
                    if DEBUG:
                        print(f"Text position set to: ({pt_to_mm(e)}mm, {pt_to_mm(f)}mm)")
            
            # When adding a fill or stroke operation
            elif token in [b'f', b'F', b'S', b's', b'B', b'b', b'b*', b'B*', b'Tj', b'TJ']:
                op_type = 'text' if token in [b'Tj', b'TJ'] else ('fill' if token in [b'f', b'F', b'b', b'B', b'b*', b'B*'] else 'stroke')
                if DEBUG:
                    print(f"{op_type.capitalize()} operation with {self.color_space} color {self.current_color}")
                    print(f"Current rectangle: {self.current_rect}")
                
                # Add operation for both regular shapes and text
                operation = {
                    'type': op_type,
                    'color': self.current_color,
                    'color_space': self.color_space,
                    'graphics_state': self.graphics_state,
                    'current_rect': self.current_rect if not self.in_text_block else None
                }
                self.operations.append(operation)
                
                # Only reset rectangle for non-text operations
                if not self.in_text_block:
                    self.current_rect = None
            
            elif token == b'Do':  # XObject reference
                # Keep the current color state for the XObject
                self.operations.append({
                    'color': self.current_color,
                    'color_space': self.color_space,
                    'type': 'xobject',
                    'graphics_state': self.graphics_state
                })
            
            i += 1
        
        return {
            'operations': self.operations,
            'is_clipping': self.is_clipping,
            'is_container': self.is_container,
            'color_space': self.color_space,
            'current_color': self.current_color,
            'graphics_state': self.graphics_state
        }

class OpacityContext:
    def __init__(self):
        self.opacity_stack = [100]  # Start with 100% opacity
        self.current_gs = None  # Track current graphics state
        
    def push_opacity(self, opacity, gs_name=None):
        self.opacity_stack.append(opacity)
        if gs_name:
            self.current_gs = gs_name
        
    def pop_opacity(self):
        if len(self.opacity_stack) > 1:  # Always keep base opacity
            self.opacity_stack.pop()
            
    def get_effective_opacity(self):
        # Multiply all opacities in the stack
        result = 100
        for opacity in self.opacity_stack:
            result = round((result * opacity) / 100)
        return result

def parse_args():
    parser = argparse.ArgumentParser(description='Analyze colors in PDF files')
    parser.add_argument('pdf_file', help='Path to the PDF file to analyze')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    return parser.parse_args()

def extract_color_values(pdf_path, debug=False):
    """Extract color values from PDF file"""
    global DEBUG
    DEBUG = debug
    
    # Reset the tracking sets for each new file
    _processed_streams = set()
    _processed_xobjects = {}
    
    cmyk_colors = defaultdict(list)
    rgb_colors = defaultdict(list)
    out_of_bounds_cmyk = defaultdict(list)
    out_of_bounds_rgb = defaultdict(list)
    color_contexts = {}
    pdf = Pdf.open(pdf_path)
    
    # Store color contexts for later use
    color_contexts = {}
    
    def is_valid_cmyk(c, m, y, k):
        """Check if CMYK values are within valid range (0-100)"""
        return all(0 <= x <= 100 for x in (c, m, y, k))
    
    def is_valid_rgb(r, g, b):
        """Check if RGB values are within valid range (0-1)"""
        return all(0 <= x <= 1 for x in (r, g, b))
    
    def is_position_within_bounds(x, y, box):
        """Check if a position is within the MediaBox boundaries"""
        try:
            x, y = float(x), float(y)
            min_x = float(box[0])
            min_y = float(box[1])
            max_x = float(box[2])
            max_y = float(box[3])
            tolerance = 1.0
            
            is_within = (min_x - tolerance <= x <= max_x + tolerance and 
                        min_y - tolerance <= y <= max_y + tolerance)
            
            if DEBUG:
                print(f"Checking position ({pt_to_mm(x)}mm, {pt_to_mm(y)}mm) against MediaBox bounds:")
                print(f"  X bounds: {pt_to_mm(min_x)}mm <= {pt_to_mm(x)}mm <= {pt_to_mm(max_x)}mm")
                print(f"  Y bounds: {pt_to_mm(min_y)}mm <= {pt_to_mm(y)}mm <= {pt_to_mm(max_y)}mm")
                print(f"  Result: {'within' if is_within else 'outside'}")
            
            return is_within
        except (ValueError, TypeError):
            return False
    
    def is_rectangle_within_bounds(x, y, w, h, box):
        """Check if any part of a rectangle overlaps with the MediaBox"""
        try:
            x, y = float(x), float(y)
            w, h = float(w), float(h)
            
            # Check all four corners
            corners = [
                (x, y),           # bottom-left
                (x + w, y),       # bottom-right
                (x, y + h),       # top-left
                (x + w, y + h)    # top-right
            ]
            
            if DEBUG:
                print(f"Checking rectangle: origin=({pt_to_mm(x)}mm, {pt_to_mm(y)}mm), size=({pt_to_mm(w)}mm, {pt_to_mm(h)}mm)")
                print(f"Corners (mm): {[(pt_to_mm(cx), pt_to_mm(cy)) for cx, cy in corners]}")
            
            # If any corner is within bounds, the rectangle is considered within bounds
            for cx, cy in corners:
                if is_position_within_bounds(cx, cy, box):
                    if DEBUG:
                        print(f"Rectangle is within bounds (corner at {pt_to_mm(cx)}mm, {pt_to_mm(cy)}mm)")
                    return True
            
            # Also check if the rectangle completely contains the MediaBox
            box_x1, box_y1, box_x2, box_y2 = [float(v) for v in box]
            if (x <= box_x1 and y <= box_y1 and 
                x + w >= box_x2 and y + h >= box_y2):
                if DEBUG:
                    print("Rectangle contains MediaBox")
                return True
            
            return False
        except (ValueError, TypeError):
            return False
    
    def analyze_color_context(content_stream, position, page):
        """Analyze the context around a color operation"""
        # Get the context around this color operation
        context_before = content_stream[max(0, position-500):position]
        context_after = content_stream[position:position+1000]
        
        # Parse the operations
        parser = PDFOperationParser()
        context = parser.parse_operations(context_after)
        
        # Add the opacity calculations
        current_opacity = 100
        parent_opacity = getattr(page, 'parent_opacity', 100)
        frame_opacity = getattr(page, 'frame_opacity', 100)
        
        # Calculate effective opacity by multiplying the entire chain
        effective_opacity = round((current_opacity * parent_opacity * frame_opacity) / 10000)
        
        if DEBUG:
            print("\nOpacity Calculation Debug:")
            print(f"Current GS opacity: {current_opacity}%")
            print(f"Parent opacity: {parent_opacity}%")
            print(f"Frame opacity: {frame_opacity}%")
            print(f"Effective opacity: {effective_opacity}%")
            print(f"Is nested: {hasattr(page, 'frame_opacity')}")
        
        text_in_bounds = False
        path_in_bounds = False
        rect_in_bounds = False
        
        is_text = False
        text_info = []
        text_position = None
        if b'BT' in context_before:
            is_text = True
            # Try to find text positioning
            text_matrix = re.findall(
                rb'([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+Tm',
                context_before
            )
            if text_matrix:
                for matrix in text_matrix:
                    x, y = float(matrix[4]), float(matrix[5])
                    text_position = (x, y)
                    if DEBUG:
                        print(f"Text position: ({pt_to_mm(x)}mm, {pt_to_mm(y)}mm)")
                    # For text, check if the position is within bounds
                    if x >= float(box[0]) and x <= float(box[2]) and y >= float(box[1]) and y <= float(box[3]):
                        text_in_bounds = True
                        if DEBUG:
                            print(f"Text is within bounds")
                    text_info.append(f"Text matrix: scale=({matrix[0]},{matrix[3]}), skew=({matrix[1]},{matrix[2]}), position=({pt_to_mm(x)}mm, {pt_to_mm(y)}mm)")
            
            # Look for text content
            text_showing = re.findall(rb'\((.*?)\)[\s]*Tj', context_after[:100])
            if text_showing:
                text_info.append(f"Text content: {text_showing[0]}")
        
        # Look for path operations
        path_ops = []
        for match in re.finditer(rb'([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([mlcvy])', context_before):
            x, y, op = match.groups()
            if is_position_within_bounds(x, y, box):
                path_in_bounds = True
            op_name = {
                b'm': 'moveto',
                b'l': 'lineto',
                b'c': 'curveto',
                b'v': 'curve',
                b'y': 'curve'
            }.get(op, 'unknown')
            path_ops.append(f"{op_name} at ({pt_to_mm(x)}mm, {pt_to_mm(y)}mm)")
        
        # Look for rectangle definitions in both before and after color
        rect_ops = []
        for match in re.finditer(
            rb'([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+([-+]?(?:\d*\.\d+|\d+\.?))[\s]+re',
            content_stream[max(0, position - 500):position + 1000]
        ):
            x, y, w, h = match.groups()
            if is_rectangle_within_bounds(x, y, w, h, box):
                rect_in_bounds = True
                if DEBUG:
                    print(f"Found rectangle within bounds: origin=({pt_to_mm(x)}mm, {pt_to_mm(y)}mm), size=({pt_to_mm(w)}mm, {pt_to_mm(h)}mm)")
            rect_ops.append(f"Rectangle: origin=({pt_to_mm(x)}mm, {pt_to_mm(y)}mm), size=({pt_to_mm(w)}mm, {pt_to_mm(h)}mm)")
        
        # Determine if the color is within bounds based on operation type
        position_in_bounds = False
        if is_text:
            position_in_bounds = text_in_bounds
        elif rect_ops:
            position_in_bounds = rect_in_bounds
        elif path_ops:
            position_in_bounds = path_in_bounds
        
        if DEBUG:
            print(f"Position in bounds: {position_in_bounds}")
            if rect_ops:
                print(f"Rectangle operations found: {rect_ops}")
        
        # Look for rendering operators after the color
        render_ops = []
        for op in re.finditer(rb'[fFSsBb\*]', context_after[:50]):
            op_name = {
                b'f': 'fill',
                b'F': 'fill',
                b'S': 'stroke',
                b's': 'close and stroke',
                b'b': 'close, fill, and stroke',
                b'B': 'fill and stroke',
                b'b*': 'close, fill*, and stroke',
                b'B*': 'fill* and stroke'
            }.get(op.group(), 'unknown')
            render_ops.append(op_name)
        
        # Look for path construction and fill operations more thoroughly
        path_ops = re.findall(rb'[0-9.-]+\s+[0-9.-]+\s+[mc][\s\n]|[lh][\s\n]', context_after[:1000])
        path_fill = bool(re.search(rb'[fFS][\s\n]', context_after[len(path_ops[0]) if path_ops else 0:1000]))
        
        # Look for clipping operations more precisely
        is_clipping = bool(re.search(rb're\s*W\s*n', context_after[:100]))
        
        # Look for container setup (q ... re W n)
        is_container = bool(re.search(rb'q.*re\s*W\s*n', context_after[:100]))
        
        # Check if this is an actual fill/stroke operation that's not part of a clipping path
        has_fill = (bool(re.search(rb're\s*f(?!\s*W)', context_after[:50])) or  # Rectangle fill (not followed by W)
                   bool(re.search(rb'h\s*f(?!\s*W)', context_after[:1000])) or   # Path fill (not followed by W)
                   (bool(path_ops) and path_fill and not is_clipping))           # Complex path with fill (not clipping)
        
        has_stroke = (bool(re.search(rb're\s*S(?!\s*W)', context_after[:50])) or  # Rectangle stroke (not followed by W)
                     bool(re.search(rb'h\s*S(?!\s*W)', context_after[:1000])))    # Path stroke (not followed by W)
        
        # Look for color space changes that might indicate actual content
        has_color_space = bool(re.search(rb'/CS\d+\s+cs', context_after[:50]))
        
        # Look for XObject operations
        has_xobject = bool(re.search(rb'/Fm\d+\s+Do', context_after[:50]))
        
        # Look for color space operations
        color_space_ops = re.findall(rb'/CS\d+\s+cs\s+[0-9.-]+\s+scn', context_after[:100])
        
        # Look for XObject usage (Do operator) specifically in this context
        has_xobject_usage = bool(re.search(rb'/Fm\d+\s+Do', context_after[:50]))
        
        if DEBUG and rect_ops:
            print("Rectangle operations (mm):")
            for rect in rect_ops:
                # Convert bytes pattern to string pattern
                nums = re.findall(r'[-+]?(?:\d*\.\d+|\d+\.?)', str(rect))
                if len(nums) >= 4:
                    x, y = pt_to_mm(float(nums[0])), pt_to_mm(float(nums[1]))
                    w, h = pt_to_mm(float(nums[2])), pt_to_mm(float(nums[3]))
                    print(f"  Origin: ({x}mm, {y}mm), Size: {w}mm x {h}mm")
        
        # Store the calculated opacities in the context
        context = {
            'is_clipping': is_clipping,
            'is_container': is_container,
            'in_clipping_sequence': in_clipping_sequence,
            'has_fill': has_fill,
            'has_stroke': has_stroke,
            'has_xobject': has_xobject_usage,  # Use the specific usage check
            'color_space_ops': color_space_ops,
            'path_ops': path_ops,
            'path_fill': path_fill,
            'render_ops': render_ops,
            'rect_ops': rect_ops,
            'position_in_bounds': position_in_bounds,
            'current_opacity': current_opacity,
            'parent_opacity': parent_opacity,
            'frame_opacity': frame_opacity,
            'effective_opacity': effective_opacity
        }
        
        return context
    
    def process_content_stream(content_stream, page, page_num, opacity_context=None):
        """Process a content stream for colors"""
        if opacity_context is None:
            opacity_context = OpacityContext()
        
        # Extract color spaces from resources and Group
        color_spaces = {}
        if hasattr(page, 'Resources'):
            color_spaces = get_color_spaces_from_resources(page.Resources)
        
        # If no color spaces found but page has Group CS, use that
        if not color_spaces and hasattr(page, 'Group') and '/CS' in page.Group:
            group_cs = str(page.Group['/CS'])
            if group_cs == '/DeviceCMYK':
                color_spaces['__default__'] = 'CMYK'
            elif group_cs == '/DeviceRGB':
                color_spaces['__default__'] = 'RGB'
            elif group_cs == '/DeviceGray':
                color_spaces['__default__'] = 'Gray'
        
        content_id = (page_num, id(content_stream))
        _processed_streams.add(content_id)
        
        if DEBUG:
            print(f"\n--- Processing content stream {content_id} ---")
            if hasattr(page, 'MediaBox'):
                box = page.MediaBox
                print(f"MediaBox: {pt_to_mm(box[0])}mm, {pt_to_mm(box[1])}mm, {pt_to_mm(box[2])}mm, {pt_to_mm(box[3])}mm")
        
        # Create parser with color space definitions
        parser = PDFOperationParser(color_spaces=color_spaces)
        context = parser.parse_operations(content_stream)
        
        # Get opacity from graphics state if available
        current_opacity = 100
        gs_name = None
        
        # Process operations
        for op in context['operations']:
            if op['type'] in ['fill', 'stroke', 'text']:  # Add 'text' to the types we process
                color = op['color']
                if color is None:
                    continue
                
                # Get the current rectangle from the operation
                current_rect = op.get('current_rect')
                if DEBUG and current_rect:
                    print(f"Processing operation with rectangle/position: {current_rect}")
                
                # For text operations, check if the position is within bounds
                position_in_bounds = True
                if op['type'] == 'text' and current_rect:
                    x, y = current_rect
                    if hasattr(page, 'MediaBox'):
                        box = page.MediaBox
                        position_in_bounds = is_position_within_bounds(x, y, box)
                        if DEBUG:
                            print(f"Text position in bounds: {position_in_bounds}")
                # For rectangles, use the existing bounds checking
                elif current_rect and len(current_rect) == 4:
                    x, y, w, h = current_rect
                    if hasattr(page, 'MediaBox'):
                        box = page.MediaBox
                        position_in_bounds = is_rectangle_within_bounds(x, y, w, h, box)
                
                # Get opacity from graphics state if available
                if hasattr(page, 'Resources') and '/ExtGState' in page.Resources:
                    gs_name = op['graphics_state'].decode('utf-8', 'ignore') if op['graphics_state'] else opacity_context.current_gs
                    if gs_name and gs_name in page.Resources['/ExtGState']:
                        gs_dict = page.Resources['/ExtGState'][gs_name]
                        if '/ca' in gs_dict:
                            current_opacity = round(float(gs_dict['/ca']) * 100)
                            if DEBUG:
                                print(f"Found opacity in ExtGState {gs_name}: {current_opacity}%")
                
                # Push current opacity to the stack
                opacity_context.push_opacity(current_opacity, gs_name)
                
                # Calculate effective opacity using the entire stack
                effective_opacity = opacity_context.get_effective_opacity()
                
                if DEBUG:
                    print(f"Opacity calculation: {' * '.join(f'{op}%' for op in opacity_context.opacity_stack)} = {effective_opacity}%")
                
                if op['color_space'] == 'CMYK':
                    color = tuple(round(c * 100) for c in color)
                    color_key = (color, effective_opacity)
                    if position_in_bounds:
                        if DEBUG:
                            print(f"Adding CMYK color {color} with rect/position {current_rect}")
                        if color_key not in cmyk_colors:
                            cmyk_colors[color_key] = ([], [], [])  # Add list for operations
                        if page_num not in cmyk_colors[color_key][0]:
                            cmyk_colors[color_key][0].append(page_num)
                        cmyk_colors[color_key][1].append(current_rect)
                        cmyk_colors[color_key][2].append(op)  # Store the operation
                    else:
                        if DEBUG:
                            print(f"Adding out-of-bounds CMYK color {color} with rect/position {current_rect}")
                        if color_key not in out_of_bounds_cmyk:
                            out_of_bounds_cmyk[color_key] = ([], [], [])  # Add list for operations
                        if page_num not in out_of_bounds_cmyk[color_key][0]:
                            out_of_bounds_cmyk[color_key][0].append(page_num)
                        out_of_bounds_cmyk[color_key][1].append(current_rect)
                        out_of_bounds_cmyk[color_key][2].append(op)  # Store the operation
                elif op['color_space'] == 'RGB':
                    color = tuple(round(c * 255) for c in color)
                    color_key = (color, effective_opacity)
                    if position_in_bounds:
                        if DEBUG:
                            print(f"Adding RGB color {color} with rect/position {current_rect}")
                        if color_key not in rgb_colors:
                            rgb_colors[color_key] = ([], [], [])  # Add list for operations
                        if page_num not in rgb_colors[color_key][0]:
                            rgb_colors[color_key][0].append(page_num)
                        rgb_colors[color_key][1].append(current_rect)
                        rgb_colors[color_key][2].append(op)  # Store the operation
                    else:
                        if DEBUG:
                            print(f"Adding out-of-bounds RGB color {color} with rect/position {current_rect}")
                        if color_key not in out_of_bounds_rgb:
                            out_of_bounds_rgb[color_key] = ([], [], [])  # Add list for operations
                        if page_num not in out_of_bounds_rgb[color_key][0]:
                            out_of_bounds_rgb[color_key][0].append(page_num)
                        out_of_bounds_rgb[color_key][1].append(current_rect)
                        out_of_bounds_rgb[color_key][2].append(op)  # Store the operation
            
                # Pop the current opacity from the stack
                opacity_context.pop_opacity()
        
        # Process any resources (XObjects)
        if hasattr(page, 'Resources'):
            process_resources(page.Resources, page, page_num, opacity_context)
    
    def process_resources(resources, page, page_num, opacity_context, nesting_level=0):
        """Recursively process resources for XObjects"""
        if not resources or "/XObject" not in resources:
            return
        
        for xobject_name in resources["/XObject"]:
            try:
                if DEBUG:
                    print(f"\n==== Processing XObject: {xobject_name} (level {nesting_level}) ====")
                obj = resources["/XObject"][xobject_name]
                
                if obj.get("/Subtype") == "/Form":
                    if DEBUG:
                        print("Found Form XObject")
                    
                    # Get the frame's opacity from the parent page's ExtGState
                    frame_opacity = 100
                    if hasattr(page, 'Resources') and '/ExtGState' in page.Resources:
                        gs_dict = page.Resources['/ExtGState']
                        if '/GS1' in gs_dict and '/ca' in gs_dict['/GS1']:  # The 80% opacity frame uses /GS1
                            frame_opacity = round(float(gs_dict['/GS1']['/ca']) * 100)
                            if DEBUG:
                                print(f"Found parent frame opacity: {frame_opacity}%")
                    
                    # Get any additional opacity from the XObject's own ExtGState
                    xobject_opacity = 100
                    if '/ExtGState' in obj.get("/Resources", {}):
                        gs_dict = obj["/Resources"]['/ExtGState']
                        for gs_name, gs_obj in gs_dict.items():
                            if '/ca' in gs_obj:
                                xobject_opacity = round(float(gs_obj['/ca']) * 100)
                                if DEBUG:
                                    print(f"Found XObject opacity: {xobject_opacity}%")
                    
                    # Create a new opacity context that inherits from the parent
                    new_opacity_context = OpacityContext()
                    new_opacity_context.opacity_stack = opacity_context.opacity_stack.copy()
                    
                    # Push both frame and XObject opacities
                    new_opacity_context.push_opacity(frame_opacity)
                    new_opacity_context.push_opacity(xobject_opacity)
                    
                    if DEBUG:
                        print(f"Current opacity stack: {new_opacity_context.opacity_stack}")
                    
                    # Process the XObject's content stream
                    if hasattr(obj, "read_bytes"):
                        content = obj.read_bytes()
                        process_content_stream(content, obj, page_num, new_opacity_context)
                        
                        # Recursively process any nested resources
                        if "/Resources" in obj:
                            process_resources(obj["/Resources"], obj, page_num, new_opacity_context, nesting_level + 1)
                    
            except Exception as e:
                if DEBUG:
                    print(f"Error processing XObject {xobject_name}: {e}")
                    import traceback
                    traceback.print_exc()
    
    # Process each page
    for page_num, page in enumerate(pdf.pages, 1):
        try:
            if DEBUG:
                print(f"\nProcessing page {page_num}")
                print(f"MediaBox: {page.MediaBox}")
                print("\nPage Resources:")
                for key in page.Resources.keys():
                    print(f"Resource key: {key}")
                    if key == '/XObject':
                        print("XObjects found:")
                        for xobj_key, xobj in page.Resources[key].items():
                            print(f"  {xobj_key}:")
                            print(f"    Type: {xobj.get('/Type', 'Not specified')}")
                            print(f"    Subtype: {xobj.get('/Subtype', 'Not specified')}")
                            if '/Resources' in xobj:
                                print(f"    Has resources: {list(xobj['/Resources'].keys())}")
            
            # Create opacity context for this page
            opacity_context = OpacityContext()
            
            # Process the main content stream
            contents = page.Contents
            if isinstance(contents, Object):
                process_content_stream(contents.read_bytes(), page, page_num, opacity_context)
            
            # Process any XObjects in the page resources
            if page.Resources:
                process_resources(page.Resources, page, page_num, opacity_context)
            
        except Exception as e:
            print(f"Debug: Error processing page {page_num}: {e}", file=sys.stderr)
            if DEBUG:
                import traceback
                traceback.print_exc()
    
    return dict(cmyk_colors), dict(rgb_colors), dict(out_of_bounds_cmyk), dict(out_of_bounds_rgb)

def convert_to_cmyk(numbers, sequence):
    """Convert color values to CMYK format"""
    # Handle grayscale to CMYK conversion
    if b'g' in sequence:
        gray = float(numbers[0])
        # Convert grayscale to CMYK (0 g = 0 0 0 100 k)
        k = round((1 - gray) * 100)
        if DEBUG:
            print(f"Converted grayscale {gray} to CMYK: (0, 0, 0, {k})")
        return (0, 0, 0, k)
    
    # Convert string numbers to floats and scale to percentages
    values = [round(float(n) * 100) for n in numbers]
    
    # Ensure we have 4 values for CMYK
    while len(values) < 4:
        values.append(0)
    
    return tuple(values[:4])

if __name__ == "__main__":
    args = parse_args()
    try:
        cmyk_colors, rgb_colors, out_of_bounds_cmyk, out_of_bounds_rgb = extract_color_values(args.pdf_file, debug=args.debug)
        
        result = {"pages": {}, "colors_in_bounds": []}
        
        # Track unique in-bounds colors
        seen_in_bounds = set()
        
        # Debug output
        if args.debug:
            print("\nCollected colors:", file=sys.stderr)
            print(f"CMYK colors: {cmyk_colors}", file=sys.stderr)
            print(f"RGB colors: {rgb_colors}", file=sys.stderr)
        
        # Combine all colors into page-based structure
        all_colors = []
        # Add CMYK colors
        for (color, opacity), (pages, rects, ops) in cmyk_colors.items():  # Note: added ops
            unique_rects = []
            unique_ops = []  # Add this
            seen = set()
            for rect, op in zip(rects, ops):  # Pair rects with ops
                rect_tuple = tuple(rect) if rect else None
                if rect_tuple not in seen:
                    seen.add(rect_tuple)
                    unique_rects.append(rect)
                    unique_ops.append(op)  # Store corresponding operation
            all_colors.append((color, opacity, pages, "CMYK", False, unique_rects, unique_ops))  # Add ops
        
        # Add RGB colors (similar changes)
        for (color, opacity), (pages, rects, ops) in rgb_colors.items():
            unique_rects = []
            unique_ops = []  # Add this
            seen = set()
            for rect, op in zip(rects, ops):  # Pair rects with ops
                rect_tuple = tuple(rect) if rect else None
                if rect_tuple not in seen:
                    seen.add(rect_tuple)
                    unique_rects.append(rect)
                    unique_ops.append(op)  # Store corresponding operation
            all_colors.append((color, opacity, pages, "RGB", False, unique_rects, unique_ops))  # Add ops
        
        # Add out of bounds colors (similar changes)
        for (color, opacity), (pages, rects, ops) in out_of_bounds_cmyk.items():
            unique_rects = []
            unique_ops = []  # Add this
            seen = set()
            for rect, op in zip(rects, ops):  # Pair rects with ops
                rect_tuple = tuple(rect) if rect else None
                if rect_tuple not in seen:
                    seen.add(rect_tuple)
                    unique_rects.append(rect)
                    unique_ops.append(op)  # Store corresponding operation
            all_colors.append((color, opacity, pages, "CMYK", True, unique_rects, unique_ops))  # Add ops
        
        for (color, opacity), (pages, rects, ops) in out_of_bounds_rgb.items():
            unique_rects = []
            unique_ops = []  # Add this
            seen = set()
            for rect, op in zip(rects, ops):  # Pair rects with ops
                rect_tuple = tuple(rect) if rect else None
                if rect_tuple not in seen:
                    seen.add(rect_tuple)
                    unique_rects.append(rect)
                    unique_ops.append(op)  # Store corresponding operation
            all_colors.append((color, opacity, pages, "RGB", True, unique_rects, unique_ops))  # Add ops
        
        # Group by page
        for color, opacity, pages, colorspace, out_of_bounds, rects, ops in all_colors:  # Note: added ops
            # Add to colors_in_bounds if not out of bounds and not seen before
            if not out_of_bounds and any(rect is not None for rect in rects):  # Only include if it has valid bounds
                color_key = (tuple(color), opacity, colorspace)
                if color_key not in seen_in_bounds:
                    seen_in_bounds.add(color_key)
                    result["colors_in_bounds"].append({
                        "colorspace": colorspace,
                        "value": list(color),
                        "opacity": opacity
                    })
            
            for page in pages:
                if str(page) not in result["pages"]:
                    result["pages"][str(page)] = {"colors": []}
                
                # Create a color entry for each rectangle or text position
                for rect, op in zip(rects, ops):  # Pair rects with ops
                    # Skip if there are no bounds
                    if rect is None:
                        continue

                    color_info = {
                        "colorspace": colorspace,
                        "value": list(color),
                        "opacity": opacity,
                        "out_of_bounds": out_of_bounds
                    }
                    
                    try:
                        if isinstance(rect, tuple) and len(rect) == 2:  # Text position
                            x, y = rect
                            color_info["bounds"] = {
                                "x": pt_to_mm(x),
                                "y": pt_to_mm(y),
                                "type": "text"
                            }
                            # Add text content if available
                            if op['type'] == 'text' and 'text_content' in op:
                                color_info["text"] = op['text_content']
                        elif isinstance(rect, tuple) and len(rect) == 4:  # Rectangle
                            x, y, w, h = rect
                            color_info["bounds"] = {
                                "x": pt_to_mm(x),
                                "y": pt_to_mm(y),
                                "width": pt_to_mm(w),
                                "height": pt_to_mm(h),
                                "type": "rectangle"
                            }
                        else:
                            # Skip if rect is not in the expected format
                            continue
                    except Exception as e:
                        if args.debug:
                            print(f"Error processing bounds {rect}: {e}", file=sys.stderr)
                        continue
                    
                    result["pages"][str(page)]["colors"].append(color_info)
        
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
