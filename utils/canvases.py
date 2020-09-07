pretty_print = {
    'pixelcanvas': "Pixelcanvas.io",
    'pixelzone': "Pixelzone.io",
    'pxlsspace': "Pxls.space"
}

url_templates = {
    'pixelcanvas': "https://pixelcanvas.io/@{0},{1}",
    'pixelzone': "http://pixelzone.io/?p={0},{1}",
    'pxlsspace': "https://pxls.space/#x={0}&y={1}"
}

center = {
    'pixelcanvas': [0, 0],
    'pixelzone': [0, 0],
    'pxlsspace': [1000, 1000]
}

PZ_CHUNK_LENGTH = 512
PC_CHUNK_LENGTH = 960

PZ_MAP_LENGTH = 8192
PZ_MAP_LENGTH_HALVED = PZ_MAP_LENGTH // 2
