from PIL import Image
import numpy as np

from utils import colors


class Color(object):
    def __init__(self, index, name, rgba):
        self.name = name
        self.rgba = rgba
        self.index = index

    def __eq__(self, other):
        if isinstance(other, EnumColor.Color):
            return self.name == other.name and self.rgba[0:3] == other.rgba[0:3]
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    @property
    def alpha(self):
        return self.rgba[3]


def name_to_array(name: str) -> np.array:
    image = Image.open(name)
    return image_to_array(image)


def image_to_array(image: Image) -> np.array:
    pixels = image.load()
    width, height = image.size

    array = np.zeros((width, height)) - 1

    for x in range(width):
        for y in range(height):
            cpixel = pixels[x, y]
            if cpixel[3] > 0:
                for c, color in enumerate(colors.pixelcanvas):
                    if cpixel[:3] == color[:3]:
                        array[x, y] = c
                        break

    return array
