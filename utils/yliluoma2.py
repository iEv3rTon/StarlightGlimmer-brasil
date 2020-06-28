from PIL import Image
from multiprocessing import Pool
import numpy as np


class Yliluoma:
    """ Class implementing yliluoma 2.2 dithering using multiprocessing.
    To use, instantiate with the order, palette and number of cores you wish to use
    for dithering. Order must be a power of 2, the palette must be a list of rgb tuples
    and cores must be an integer. Cores is used to control the number of processes
    used, for best results use the amount of CPU cores you have on your machine.
    Then call dither with the PIL image object you wish to have dithered.
    """
    GAMMA = 2.2
    LIMIT = 16
    LUMA_VALUES = [299, 587, 114]

    def __init__(self, order, palette):
        self.order = order
        self.matrix = self.create_matrix(order)
        self.matrix = self.matrix.tolist()
        self.palette = palette
        self.luma = \
            [[cha * lum for cha, lum in zip(channels, self.LUMA_VALUES)] for channels in palette]
        self.luma = [r + g + b for r, g, b in self.luma]
        self.palette_gamma = \
            [[self.gamma_correct(channel / 255) for channel in color] for color in palette]

    def create_matrix(self, order):
        """Get the index matrix with side of length n.
        Will only work if n is a power of 2.
        :param int n: Power of 2 side length of matrix.
        :return: The index matrix.
        """
        if order == 2:
            return np.array([[0, 3], [2, 1]], 'int')
        else:
            smaller_matrix = self.create_matrix(order >> 1)
            return np.bmat([[4 * smaller_matrix, 4 * smaller_matrix + 3],
                            [4 * smaller_matrix + 2, 4 * smaller_matrix + 1]])

    @staticmethod
    def gamma_correct(value):
        return value ** Yliluoma.GAMMA

    @staticmethod
    def gamma_uncorrect(value):
        return value ** (1 / Yliluoma.GAMMA)

    @staticmethod
    def calc_luma(color):
        r, g, b = [channel * luma for channel, luma in zip(color, Yliluoma.LUMA_VALUES)]
        return (r + g + b) / (255 * 1000)

    def get_luma(self, color_index):
        return self.luma[color_index]

    @staticmethod
    def calc_diff(c1, c2, channel):
        diff_sq = ((c1 - c2) / 255) ** 2
        lumas = {i: lum / 1000 for i, lum in enumerate(Yliluoma.LUMA_VALUES)}
        return diff_sq * lumas.get(channel)

    def color_compare(self, color1, color2):
        luma1 = self.calc_luma(color1)
        luma2 = self.calc_luma(color2)
        lumadiff_sq = (luma1 - luma2) ** 2
        diff_r, diff_g, diff_b = \
            [self.calc_diff(*channels, i) for i, channels in enumerate(zip(color1, color2))]
        return (diff_r + diff_g + diff_b) * 0.75 + lumadiff_sq

    def calc_mixing_plan(self, color):
        so_far = [0, 0, 0]
        result = []

        while len(result) < Yliluoma.LIMIT:
            chosen_amount = 1
            max_test_count = max(1, len(result))
            least_penalty = -1

            for index, _pal_color in enumerate(self.palette):
                sum = so_far
                add = self.palette_gamma[index]

                for p in multiply_sequence(1, 2, max_test_count):
                    sum = [s + add[i] for i, s in enumerate(sum)]
                    add = [a + add[i] for i, a in enumerate(add)]
                    t = len(result) + p

                    test = [self.gamma_uncorrect(s / t) * 255 for s in sum]
                    penalty = self.color_compare(color, test)

                    if penalty < least_penalty or least_penalty < 0:
                        least_penalty = penalty
                        chosen = index
                        chosen_amount = p

            # Append the index of the chosen colour to result, chosen_amount n.o. times
            result += [chosen for _ in range(chosen_amount)]
            # Must be something to do with keeping track of gamma? So the image isn't too bright?
            so_far = [c + (self.palette_gamma[chosen][i] * chosen_amount) for i, c in enumerate(so_far)]

        result.sort(key=lambda color: self.get_luma(color))
        return result

    def _map_call(self, x, y, color):
        return x, y, self.calc_mixing_plan(color)

    def dither(self, image):
        image = image.convert("RGB")
        output = Image.new("RGB", (image.width, image.height))
        pixels = [[x, y, image.getpixel((x, y))] for y in range(image.height) for x in range(image.width)]

        with Pool() as pool:
            for x, y, plan in pool.starmap(self._map_call, pixels):
                # Below math creates values from 0 through order^2 - 1 according to the current x and y
                index = (x & (self.order - 1)) + ((y & (self.order - 1)) << 3)
                # convert 1d index back to 2d coordinates useful for matrix
                i = index % self.order
                j = index // self.order
                matrix_value = self.matrix[i][j] * len(plan) // 64
                output.putpixel((x, y), self.palette[plan[matrix_value]])

        return output


def multiply_sequence(start, factor, max):
    while start <= max:
        yield start
        start *= factor
