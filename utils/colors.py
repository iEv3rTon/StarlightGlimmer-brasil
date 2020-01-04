pixelcanvas = [
    (255, 255, 255),  # White
    (228, 228, 228),  # Light Grey
    (136, 136, 136),  # Dark Grey
    ( 34,  34,  34),  # Black
    (255, 167, 209),  # Pink
    (229,   0,   0),  # Red
    (229, 149,   0),  # Orange
    (160, 106,  66),  # Brown
    (229, 217,   0),  # Yellow
    (148, 224,  68),  # Light Green
    (  2, 190,   1),  # Green
    (  0, 211, 221),  # Cyan
    (  0, 131, 199),  # Teal
    (  0,   0, 234),  # Blue
    (207, 110, 228),  # Light Purple
    (130,   0, 128)   # Purple
]

pcDitherColours = {
    'white':      (255, 255, 255, 255),
    'light grey': (228, 228, 228, 255),
    'grey':       (136, 136, 136, 255),
    'black':      ( 34,  34,  34, 255),
    'pink':       (255, 167, 209, 255),
    'red':        (229,   0,   0, 255),
    'orange':     (229, 149,   0, 255),
    'brown':      (160, 106,  66, 255),
    'yellow':     (229, 217,   0, 255),
    'lime':       (148, 224,  68, 255),
    'green':      (  2, 190,   1, 255),
    'cyan':       (  0, 211, 221, 255),
    'light blue': (  0, 131, 199, 255),
    'blue':       (  0,   0, 234, 255),
    'violet':     (207, 110, 228, 255),
    'purple':     (130,   0, 128, 255)
}

pcClashes = (
    ["white", "grey"], ["white", "black"], ["white", "red"], ["white", "brown"],
    ["white", "green"], ["white", "blue"], ["white", "purple"],
    ["light grey", "black"], ["light grey", "red"], ["light grey", "brown"],
    ["light grey", "green"], ["light grey", "blue"], ["light grey", "purple"],
    ["grey", "cyan"], ["grey", "blue"], ["grey", "purple"],
    ["black", "pink"], ["black", "orange"], ["black", "yellow"], ["black", "lime"],
    ["black", "green"], ["black", "cyan"], ["black", "light blue"], ["black", "violet"],
    ["pink", "red"], ["pink", "brown"], ["pink", "green"], ["pink", "cyan"], ["pink", "light blue"], ["pink", "blue"],
    ["red", "orange"], ["red", "yellow"], ["red", "lime"], ["red", "green"], ["red", "cyan"], ["red", "light blue"],
    ["orange", "green"], ["orange", "cyan"], ["orange", "light blue"], ["orange", "blue"], ["orange", "purple"],
    ["brown", "yellow"], ["brown", "light green"], ["brown", "cyan"], ["brown", "blue"], ["brown", "purple"],
    ["yellow", "green"], ["yellow", "cyan"], ["yellow", "light blue"], ["yellow", "blue"], ["yellow", "violet"], ["yellow", "purple"],
    ["lime", "light blue"], ["lime", "blue"], ["lime", "violet"], ["lime", "purple"],
    ["green", "blue"], ["green", "violet"], ["green", "purple"],
    ["cyan", "blue"], ["cyan", "violet"], ["cyan", "purple"],
    ["light blue", "purple"]
)

pixelzone = [
    ( 38,  38,	38),  # Dark Grey
    (  0,	0,	 0),  # Black
    (128, 128, 128),  # Light Grey
    (255, 255, 255),  # White
    (153,  98,  61),  # Brown
    (255, 163, 200),  # Pink
    (207, 115, 230),  # Light Purple
    (128,   0, 128),  # Purple
    (229,   0,   0),  # Red
    (229, 137,   0),  # Orange
    (229, 229,   0),  # Yellow
    (150, 230,  70),  # Light Green
    (  0, 190,   0),  # Green
    (  0, 230, 230),  # Cyan
    (  0, 136, 204),  # Teal
    (  0,   0, 230)   # Blue
]

pxlsspace = [
    (255, 255, 255),  # White
    (205, 205, 205),  # Light Grey
    (136, 136, 136),  # Med Light Grey
    ( 85,  85,  85),  # Med Dark Grey
    ( 34,  34,  34),  # Dark Grey
    (  0,   0,   0),  # Black
    (255, 167, 209),  # Pink
    (229,   0,   0),  # Red
    (128,   0,   0),  # Dark Red
    (255, 221, 202),  # Beige
    (246, 179, 137),  # Tan
    (229, 149,   0),  # Orange
    (160, 106,  66),  # Light Brown
    ( 96,  64,  40),  # Dark Brown
    (229, 217,   0),  # Yellow
    (148, 224,  68),  # Light Green
    (  2, 190,   1),  # Green
    (  0,  95,   0),  # Dark Green
    (  0, 211, 221),  # Cyan
    (  0, 131, 199),  # Teal
    (  0,   0, 234),  # Blue
    (207, 110, 228),  # Lavender
    (255,   0, 255),  # Magenta
    (102,   3,  60)   # Purple
]

by_name = {
    "pixelcanvas": pixelcanvas,
    "pixelzone": pixelzone,
    "pxlsspace": pxlsspace
}
