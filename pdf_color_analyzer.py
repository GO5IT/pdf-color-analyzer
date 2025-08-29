import sys
from pikepdf import Pdf, Object
import re
from collections import defaultdict
import json
import argparse
import logging

def setup_logging(debug_mode):
    """Configure logging based on debug mode"""
    level = logging.DEBUG if debug_mode else logging.WARNING
    logging.basicConfig(
        level=level,
        format='%(message)s'
    )

def debug_log(message):
    """Wrapper for debug logging"""
    logging.debug(message)

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
            debug_log(f"Found Group color space: {group_cs}")
            if group_cs == '/DeviceCMYK':
                cs_dict['__default__'] = 'CMYK'
            elif group_cs == '/DeviceRGB':
                cs_dict['__default__'] = 'RGB'
            elif group_cs == '/DeviceGray':
                cs_dict['__default__'] = 'Gray'
    
    if resources:
        debug_log("\nResource keys available:" + str(resources.keys()))
        debug_log("\nDetailed Resource Contents:")
        for key in resources.keys():
            debug_log(f"\n{key}:")
            try:
                if isinstance(resources[key], dict):
                    for subkey, value in resources[key].items():
                        debug_log(f"  {subkey}: {value}")
                        if isinstance(value, dict):
                            for k, v in value.items():
                                debug_log(f"    {k}: {v}")
                else:
                    debug_log(f"  {resources[key]}")
            except Exception as e:
                debug_log(f"  Error accessing resource: {e}")
        
        if '/ColorSpace' in resources:
            for cs_name, cs_value in resources['/ColorSpace'].items():
                try:
                    # Convert pikepdf.Object to list if possible
                    if isinstance(cs_value, (list, tuple)):
                        array_items = cs_value
                    else:
                        try:
                            array_items = list(cs_value)
                        except Exception as e:
                            debug_log(f"Could not convert to list: {e}")
                            continue
                    
                    base_cs = str(array_items[0])
                    debug_log(f"Processing array-based color space with base: {base_cs}")
                    
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
                            debug_log(f"ICC profile with {n_components} components")
                            if n_components == 4:
                                cs_dict[cs_name] = 'CMYK'
                            elif n_components == 3:
                                cs_dict[cs_name] = 'RGB'
                            elif n_components == 1:
                                cs_dict[cs_name] = 'Gray'
                        except Exception as e:
                            debug_log(f"Error getting ICC components: {e}")
                    elif base_cs == '/DeviceN':
                        if len(array_items) >= 3:
                            alternate_cs = str(array_items[2])
                            debug_log(f"DeviceN with alternate color space: {alternate_cs}")
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
                                        debug_log(f"DeviceN process color space: {process_cs}")
                                        if process_cs == '/DeviceCMYK':
                                            cs_dict[cs_name] = 'CMYK'
                                        elif process_cs == '/DeviceRGB':
                                            cs_dict[cs_name] = 'RGB'
                                        elif process_cs == '/DeviceGray':
                                            cs_dict[cs_name] = 'Gray'
                    else:
                        debug_log(f"Warning: Unknown color space base: {base_cs}")
                except Exception as e:
                    debug_log(f"Error processing color space {cs_name}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue
    
    debug_log(f"\nExtracted color spaces from resources: {cs_dict}")
    return cs_dict

class PDFOperationParser:
    def __init__(self, color_spaces=None):
        self.stack = []
        self.current_color = None
        self.color_space = None
        self.operations = []
        self.graphics_state = None
        self.current_rect = None
        self.color_spaces = color_spaces or {}
        self.in_text_block = False
        self.current_text_position = None
        self.current_text_content = None
        
        if '__default__' in self.color_spaces:
            self.color_space = self.color_spaces['__default__']
            debug_log(f"Using default color space: {self.color_space}")
    
    def _parse_tokens(self, content):
        """Extract tokens from PDF content stream"""
        tokens = re.findall(rb'''
            \([^)]*\)          # Text content in parentheses
            |/\w+              # Names starting with /
            |[-+]?\d*\.?\d+   # Numbers (integer or float)
            |[A-Za-z]+         # Operators
            |[\[\]{}()]        # Other special characters
            |\s+               # Whitespace
        ''', content, re.VERBOSE)
        
        debug_log("\nAll tokens:")
        debug_log(tokens)
        
        return tokens

    def _parse_text_array(self, tokens, start_index):
        """Process a text array starting at the given index and return the assembled text and new index"""
        debug_log("Starting text array processing")
        
        text_parts = []
        i = start_index
        
        while i < len(tokens) and tokens[i].strip() != b']':
            current_token = tokens[i].strip()
            
            # Check if this is a text string (starts with parenthesis)
            if current_token.startswith(b'(') and current_token.endswith(b')'):
                # Remove the parentheses and decode
                text = current_token[1:-1].decode('utf-8', errors='replace')
                debug_log(f"Found text part: {text}")
                text_parts.append(text)
            
            i += 1
        
        # Join text parts without extra spaces
        text_content = ''.join(text_parts).replace('  ', ' ').strip()
        debug_log(f"Assembled text content: {text_content}")
        
        return text_content, i + 1  # i + 1 to skip the closing bracket

    def _handle_text_block(self, token):
        """Handle text block operations (BT/ET)"""
        if token == b'BT':
            self.in_text_block = True
            debug_log("Entering text block")
        elif token == b'ET':
            self.in_text_block = False
            debug_log("Exiting text block")

    def _handle_text_position(self, token):
        """Handle text position operations (Tm)"""
        if len(self.stack) >= 6:
            # Tm takes 6 numbers: a b c d e f
            # where e and f are the x,y position
            f = self.stack.pop()  # y position
            e = self.stack.pop()  # x position
            self.stack = self.stack[:-4]  # Remove a b c d
            self.current_text_position = (e, f)
            debug_log(f"Text position set to: ({pt_to_mm(e)}mm, {pt_to_mm(f)}mm)")

    def _handle_text_operation(self, token):
        """Handle text showing operations (Tj/TJ)"""
        debug_log(f"Text operation with {self.color_space} color {self.current_color}")
        debug_log(f"Text position: {self.current_text_position}")
        debug_log(f"Text content: {self.current_text_content}")
        
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
        
        debug_log(f"Added operation with text: {self.current_text_content}")
        
        self.current_text_content = None  # Reset text content

    def _handle_color_space_operation(self, tokens, i):
        """Handle color space operations and return the new index"""
        color_space_name = tokens[i].decode('utf-8', 'ignore')
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
            
            debug_log(f"Color space changed to: {self.color_space} for {color_space_name}")
            i += 1
        
        return i

    def _handle_rectangle(self):
        """Handle rectangle operation and store rectangle parameters"""
        if len(self.stack) >= 4:
            h = float(self.stack.pop())
            w = float(self.stack.pop())
            y = float(self.stack.pop())
            x = float(self.stack.pop())
            self.current_rect = (x, y, w, h)  # Store the current rectangle
            debug_log(f"Rectangle: {pt_to_mm(x)}mm, {pt_to_mm(y)}mm, {pt_to_mm(w)}mm x {pt_to_mm(h)}mm")
            debug_log(f"Storing rectangle: {self.current_rect}")

    def _handle_rgb_color(self, token):
        """Handle RGB color operations (rg/RG)"""
        if len(self.stack) >= 3:
            b = self.stack.pop()
            g = self.stack.pop()
            r = self.stack.pop()
            self.current_color = (r, g, b)
            self.color_space = 'RGB'
            rgb_255 = tuple(round(c * 255) for c in self.current_color)
            debug_log(f"RGB color via {token}: {rgb_255}")

    def _handle_cmyk_color(self, token, stack_size=4):
        """Handle CMYK color operations (k/K/sc/SC/scn/SCN)"""
        if len(self.stack) >= stack_size:
            if stack_size == 1:  # For the case of '1 scn'
                value = self.stack.pop()
                if value == 1:
                    self.current_color = (0, 0, 0, 1)
                    debug_log(f"Single value {token} interpreted as black: {self.current_color}")
                    return
                # If not a special case, restore the value and continue with normal processing
                self.stack.append(value)
                return
            
            k = self.stack.pop()
            y = self.stack.pop()
            m = self.stack.pop()
            c = self.stack.pop()
            self.current_color = (c, m, y, k)
            self.color_space = 'CMYK'
            debug_log(f"CMYK color via {token}: {self.current_color}")

    def _handle_scene_color(self, token):
        """Handle all color operations (sc/SC/scn/SCN) based on current color space"""
        # First ensure we have a color space
        if self.color_space is None:
            raise ValueError(f"Color operation {token} encountered but no color space has been set")
        
        if self.color_space == 'RGB':
            self._handle_rgb_color(token)
        elif self.color_space == 'CMYK':
            self._handle_cmyk_color(token)

    def _handle_grayscale_color(self, token):
        """Handle Grayscale color operations (g/G)"""
        if len(self.stack) >= 1:
            gray = self.stack.pop()
            self.current_color = (gray,)  # Single value for grayscale
            self.color_space = 'Gray'
            debug_log(f"Grayscale color via {token}: {gray}")

    def _handle_fill_stroke_operation(self, token):
        """Handle fill and stroke operations (f/F/S/s/B/b/b*/B*)"""
        op_type = 'fill' if token in [b'f', b'F', b'b', b'B', b'b*', b'B*'] else 'stroke'
        
        debug_log(f"{op_type.capitalize()} operation with {self.color_space} color {self.current_color}")
        debug_log(f"Current rectangle: {self.current_rect}")
        
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

    def _handle_xobject(self, token):
        """Handle XObject operations (Do)"""
        # Keep the current color state for the XObject
        operation = {
            'color': self.current_color,
            'color_space': self.color_space,
            'type': 'xobject',
            'graphics_state': self.graphics_state
        }
        self.operations.append(operation)

    def parse_operations(self, content):
        tokens = self._parse_tokens(content)
        
        i = 0
        while i < len(tokens):
            token = tokens[i].strip()
            if not token:
                i += 1
                continue

            # Handle text array operations
            if token == b'[':
                self.current_text_content, i = self._parse_text_array(tokens, i + 1)
                continue

            # Handle text block operations
            if token in [b'BT', b'ET']:
                self._handle_text_block(token)
                i += 1
                continue

            # Handle text position operations
            if token == b'Tm':
                self._handle_text_position(token)
                i += 1
                continue

            # Handle text showing operations
            if token in [b'Tj', b'TJ']:
                self._handle_text_operation(token)
                i += 1
                continue

            # Handle CMYK, Grayscale, and RGB color operations
            if token in [b'k', b'K']:
                self._handle_cmyk_color(token)
                i += 1
                continue
            elif token in [b'g', b'G']:
                self._handle_grayscale_color(token)
                i += 1
                continue
            elif token in [b'rg', b'RG']:
                self._handle_rgb_color(token)
                i += 1
                continue

            # Handle fill and stroke operations
            if token in [b'f', b'F', b'S', b's', b'B', b'b', b'b*', b'B*']:
                self._handle_fill_stroke_operation(token)
                i += 1
                continue

            # Handle color space operations
            if token.startswith(b'/CS') or token.startswith(b'/Device'):
                i = self._handle_color_space_operation(tokens, i)
                continue
            
            # Handle scene color operations
            if token in [b'sc', b'SC', b'scn', b'SCN']:
                if self.color_space is None:
                    debug_log(f"Warning: Color operation {token} encountered but no color space has been set")
                    i += 1
                    continue
                self._handle_scene_color(token)
                i += 1
                continue

            # Handle XObject operations
            if token == b'Do':
                self._handle_xobject(token)
                i += 1
                continue

            # Handle graphics state operations
            if token.startswith(b'/GS'):
                self.graphics_state = token
                i += 2
                continue

            # Handle rectangle operations
            if token == b're':
                self._handle_rectangle()
                i += 1
                continue

            # Handle numeric values
            if re.match(rb'[+-]?(?:\d*\.\d+|\d+\.?)', token):
                self.stack.append(float(token))
                i += 1
                continue

            i += 1

        return {
            'operations': self.operations,
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
    args = parser.parse_args()
    setup_logging(args.debug)
    return args

def _add_color_to_dict(color_dict, color_key, page_num, current_rect, op):
    """Add color operation to a dictionary"""
    if color_key not in color_dict:
        color_dict[color_key] = ([], [], [])
    if page_num not in color_dict[color_key][0]:
        color_dict[color_key][0].append(page_num)
    color_dict[color_key][1].append(current_rect)
    color_dict[color_key][2].append(op)

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
            
            debug_log(f"Checking position ({pt_to_mm(x)}mm, {pt_to_mm(y)}mm) against MediaBox bounds:")
            debug_log(f"  X bounds: {pt_to_mm(min_x)}mm <= {pt_to_mm(x)}mm <= {pt_to_mm(max_x)}mm")
            debug_log(f"  Y bounds: {pt_to_mm(min_y)}mm <= {pt_to_mm(y)}mm <= {pt_to_mm(max_y)}mm")
            debug_log(f"  Result: {'within' if is_within else 'outside'}")
            
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
            
            debug_log(f"Checking rectangle: origin=({pt_to_mm(x)}mm, {pt_to_mm(y)}mm), size=({pt_to_mm(w)}mm, {pt_to_mm(h)}mm)")
            debug_log(f"Corners (mm): {[(pt_to_mm(cx), pt_to_mm(cy)) for cx, cy in corners]}")
            
            # If any corner is within bounds, the rectangle is considered within bounds
            for cx, cy in corners:
                if is_position_within_bounds(cx, cy, box):
                    debug_log(f"Rectangle is within bounds (corner at {pt_to_mm(cx)}mm, {pt_to_mm(cy)}mm)")
                    return True
            
            # Also check if the rectangle completely contains the MediaBox
            box_x1, box_y1, box_x2, box_y2 = [float(v) for v in box]
            if (x <= box_x1 and y <= box_y1 and 
                x + w >= box_x2 and y + h >= box_y2):
                debug_log("Rectangle contains MediaBox")
                return True
            
            return False
        except (ValueError, TypeError):
            return False
    
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
        
        debug_log(f"\n--- Processing content stream ---")
        if hasattr(page, 'MediaBox'):
            try:
                box = page.MediaBox
                debug_log(f"MediaBox: {pt_to_mm(box[0])}mm, {pt_to_mm(box[1])}mm, {pt_to_mm(box[2])}mm, {pt_to_mm(box[3])}mm")
            except Exception as e:
                debug_log(f"MediaBox: Unable to display MediaBox ({e})")
        
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
                if current_rect:
                   debug_log(f"Processing operation with rectangle/position: {current_rect}")
                
                # For text operations, check if the position is within bounds
                position_in_bounds = True
                if op['type'] == 'text' and current_rect:
                    x, y = current_rect
                    if hasattr(page, 'MediaBox'):
                        box = page.MediaBox
                        position_in_bounds = is_position_within_bounds(x, y, box)
                        debug_log(f"Text position in bounds: {position_in_bounds}")
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
                            debug_log(f"Found opacity in ExtGState {gs_name}: {current_opacity}%")
                
                # Push current opacity to the stack
                opacity_context.push_opacity(current_opacity, gs_name)
                
                # Calculate effective opacity using the entire stack
                effective_opacity = opacity_context.get_effective_opacity()
                
                debug_log(f"Opacity calculation: {' * '.join(f'{op}%' for op in opacity_context.opacity_stack)} = {effective_opacity}%")
                
                if op['color_space'] in ['CMYK', 'RGB']:
                    # Color space specific multiplier and target dict
                    multiplier = 100 if op['color_space'] == 'CMYK' else 255
                    target_dict = (cmyk_colors if position_in_bounds else out_of_bounds_cmyk) if op['color_space'] == 'CMYK' else (rgb_colors if position_in_bounds else out_of_bounds_rgb)
                    
                    color = tuple(round(c * multiplier) for c in color)
                    color_key = (color, effective_opacity)
                    
                    debug_log(f"Adding {'in-bounds' if position_in_bounds else 'out-of-bounds'} {op['color_space']} color {color} with rect/position {current_rect}")
                    
                    _add_color_to_dict(target_dict, color_key, page_num, current_rect, op)
            
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
                debug_log(f"\n==== Processing XObject: {xobject_name} (level {nesting_level}) ====")
                obj = resources["/XObject"][xobject_name]
                
                if obj.get("/Subtype") == "/Form":
                    debug_log("Found Form XObject")
                    
                    # Get the frame's opacity from the parent page's ExtGState
                    frame_opacity = 100
                    if hasattr(page, 'Resources') and '/ExtGState' in page.Resources:
                        gs_dict = page.Resources['/ExtGState']
                        if '/GS1' in gs_dict and '/ca' in gs_dict['/GS1']:
                            frame_opacity = round(float(gs_dict['/GS1']['/ca']) * 100)
                            debug_log(f"Found parent frame opacity: {frame_opacity}%")
                    
                    # Get any additional opacity from the XObject's own ExtGState
                    xobject_opacity = 100
                    if '/ExtGState' in obj.get("/Resources", {}):
                        gs_dict = obj["/Resources"]['/ExtGState']
                        for gs_name, gs_obj in gs_dict.items():
                            if '/ca' in gs_obj:
                                xobject_opacity = round(float(gs_obj['/ca']) * 100)
                                debug_log(f"Found XObject opacity: {xobject_opacity}%")
                    
                    # Create a new opacity context that inherits from the parent
                    new_opacity_context = OpacityContext()
                    new_opacity_context.opacity_stack = opacity_context.opacity_stack.copy()
                    
                    # Push both frame and XObject opacities
                    new_opacity_context.push_opacity(frame_opacity)
                    new_opacity_context.push_opacity(xobject_opacity)
                    
                    debug_log(f"Current opacity stack: {new_opacity_context.opacity_stack}")
                    
                    # Process the XObject's content stream
                    if hasattr(obj, "read_bytes"):
                        content = obj.read_bytes()
                        process_content_stream(content, obj, page_num, new_opacity_context)
                        
                        # Recursively process any nested resources
                        if "/Resources" in obj:
                            process_resources(obj["/Resources"], obj, page_num, new_opacity_context, nesting_level + 1)
                
            except Exception as e:
                debug_log(f"Error processing XObject {xobject_name}: {e}")
                import traceback
                traceback.print_exc()
    
    # Process each page
    for page_num, page in enumerate(pdf.pages, 1):
        try:
            debug_log(f"\nProcessing page {page_num}")
            try:
                box = page.MediaBox
                debug_log(f"MediaBox: {pt_to_mm(box[0])}mm, {pt_to_mm(box[1])}mm, {pt_to_mm(box[2])}mm, {pt_to_mm(box[3])}mm")
            except Exception as e:
                debug_log(f"MediaBox: Unable to display MediaBox ({e})")
            debug_log("\nPage Resources:")
            for key in page.Resources.keys():
                debug_log(f"Resource key: {key}")
                if key == '/XObject':
                    debug_log("XObjects found:")
                    for xobj_key, xobj in page.Resources[key].items():
                        debug_log(f"  {xobj_key}:")
                        debug_log(f"    Type: {xobj.get('/Type', 'Not specified')}")
                        debug_log(f"    Subtype: {xobj.get('/Subtype', 'Not specified')}")
                        if '/Resources' in xobj:
                            debug_log(f"    Has resources: {list(xobj['/Resources'].keys())}")
            
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
            debug_log(f"Error processing page {page_num}: {e}")
            import traceback
            traceback.print_exc()
    
    return dict(cmyk_colors), dict(rgb_colors), dict(out_of_bounds_cmyk), dict(out_of_bounds_rgb)

def _process_color_dict(color_dict, color_space, is_out_of_bounds):
    """Process a color dictionary and return list of unique color operations"""
    results = []
    for (color, opacity), (pages, rects, ops) in color_dict.items():
        unique_rects = []
        unique_ops = []
        seen = set()
        
        for rect, op in zip(rects, ops):
            rect_tuple = tuple(rect) if rect else None
            if rect_tuple not in seen:
                seen.add(rect_tuple)
                unique_rects.append(rect)
                unique_ops.append(op)
        
        results.append((color, opacity, pages, color_space, is_out_of_bounds, unique_rects, unique_ops))
    return results

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
        all_colors.extend(_process_color_dict(cmyk_colors, "CMYK", False))
        all_colors.extend(_process_color_dict(rgb_colors, "RGB", False))
        all_colors.extend(_process_color_dict(out_of_bounds_cmyk, "CMYK", True))
        all_colors.extend(_process_color_dict(out_of_bounds_rgb, "RGB", True))
        
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
