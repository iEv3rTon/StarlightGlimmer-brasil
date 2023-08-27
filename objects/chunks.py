import abc
import io
import zlib
import uuid

from PIL import Image

from utils import colors
from utils.canvases import PZ_CHUNK_LENGTH, PC_CHUNK_LENGTH, PZ_MAP_LENGTH_HALVED


class Chunky(abc.ABC):
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self._image = None

        self.uuid = uuid.uuid4().hex

    @property
    @abc.abstractmethod
    def height(self):
        pass

    @property
    def image(self):
        return self._image

    @property
    @abc.abstractmethod
    def p_x(self):
        pass

    @property
    @abc.abstractmethod
    def p_y(self):
        pass

    @property
    @abc.abstractmethod
    def url(self):
        pass

    @abc.abstractmethod
    def is_in_bounds(self):
        pass

    @abc.abstractmethod
    def load(self, data):
        pass

    @staticmethod
    @abc.abstractmethod
    def get_intersecting(x, y, dx, dy):
        pass

    @property
    @abc.abstractmethod
    def width(self):
        pass

    def __eq__(self, other):
        if type(other) is BigChunk or type(other is ChunkPz):
            return self.y == other.y and self.x == other.x
        return False

    def __hash__(self):
        return hash((self.x, self.y))


class BigChunk(Chunky):
    palette = [x for sub in colors.pixelcanvas for x in sub] * 16

    @property
    def height(self):
        return PC_CHUNK_LENGTH

    @property
    def width(self):
        return PC_CHUNK_LENGTH

    @property
    def p_x(self):
        # TODO: Don't understand what this is acheieving, but only check uses it, so it's fine if that breaks temporarily
        # it's probably something to do with where a coord inside the chunk is relative to the map
        return self.x * PC_CHUNK_LENGTH - 448

    @property
    def p_y(self):
        return self.y * PC_CHUNK_LENGTH - 448

    @property
    def url(self):
        return "https://pixelcanvas.io/tile/{0}/{1}.png".format(self.x * 512, self.y * 512)

    def is_in_bounds(self):
        # TODO: These aren't the map boundaries on pixelcanvas. I'm not sure there are boundaries anymore, it's infinite iirc?
        return -1043 <= self.x < 1043 and -1043 <= self.y < 1043

    def load(self, data):
        image = Image.open(io.BytesIO(data))
        self._image = image
        return image

    @staticmethod
    def get_intersecting(x, y, dx, dy):
        # dx and dy are the width and height of the area being fetched
        bigchunks = []
        x = x // PC_CHUNK_LENGTH
        y = y // PC_CHUNK_LENGTH
        dx = (x + dx) // PC_CHUNK_LENGTH
        dy = (y + dy) // PC_CHUNK_LENGTH
        for iy in range(y, dy + 1):
            for ix in range(x, dx + 1):
                bigchunks.append(BigChunk(ix, iy))
        return bigchunks, (dx - x + 1, dy - y + 1)


class ChunkPz(Chunky):
    palette = [x for sub in colors.pixelzone for x in sub] * 16

    @property
    def height(self):
        return 512

    @property
    def p_x(self):
        return self.x * PZ_CHUNK_LENGTH - PZ_MAP_LENGTH_HALVED

    @property
    def p_y(self):
        return self.y * PZ_CHUNK_LENGTH - PZ_MAP_LENGTH_HALVED

    @property
    def url(self):
        return '42["getChunk", {{"x": {0}, "y": {1}}}]'.format(self.x, self.y)

    @property
    def width(self):
        return PZ_CHUNK_LENGTH

    def is_in_bounds(self):
        return 0 <= self.x < 16 and 0 <= self.y < 16

    @classmethod
    def load(cls, data):
        data = zlib.decompress(data)
        image = Image.frombytes('P', (PZ_CHUNK_LENGTH, PZ_CHUNK_LENGTH), data, 'raw', 'P;4')
        image.putpalette(cls.palette)
        return image

    @staticmethod
    def get_intersecting(x, y, dx, dy):
        x += PZ_MAP_LENGTH_HALVED
        y += PZ_MAP_LENGTH_HALVED
        chunks = []
        dx, dy = ChunkPz.chunk_from_coords(x + dx, y + dy)
        x, y = ChunkPz.chunk_from_coords(x, y)
        for iy in range(y, dy + 1):
            for ix in range(x, dx + 1):
                chunks.append(ChunkPz(ix, iy))
        return chunks, (dx - x + 1, dy - y + 1)

    @staticmethod
    def chunk_from_coords(x, y):
        return x // PZ_CHUNK_LENGTH, y // PZ_CHUNK_LENGTH


class PxlsBoard(Chunky):
    palette = [x for sub in colors.pxlsspace for x in sub]

    def __init__(self):
        super().__init__(0, 0)
        self._info = None

    @property
    def height(self):
        return self._info['height']

    @property
    def p_x(self):
        return 0

    @property
    def p_y(self):
        return 0

    @property
    def url(self):
        return None

    @property
    def width(self):
        return self._info['width']

    def is_in_bounds(self):
        return True

    def load(self, data):
        self._image = Image.frombytes("P", (self._info['width'], self._info['height']), data, 'raw', 'P', 0, 1)
        self._image.putpalette(self.palette)

    def set_board_info(self, info):
        self._info = info

    @staticmethod
    def get_intersecting(x, y, dx, dy):
        return [PxlsBoard()], (1, 1)
