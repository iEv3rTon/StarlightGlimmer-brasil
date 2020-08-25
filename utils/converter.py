from PIL import Image
import numpy as np

from utils import colors


def image_to_array(image: Image, canvas: str) -> np.array:
    pixels = image.load()
    width, height = image.size
    palette = colors.by_name[canvas]

    array = np.zeros((width, height), dtype=np.uint8) - 1

    for x in range(width):
        for y in range(height):
            cpixel = pixels[x, y]
            if cpixel[3] > 0:
                for c, color in enumerate(palette):
                    if cpixel[:3] == color[:3]:
                        array[x, y] = c
                        break

    return array
