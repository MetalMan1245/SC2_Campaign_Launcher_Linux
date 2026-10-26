"""Read bounded script members from MPQ archives without extracting game assets."""

import bz2
import struct
import zlib

try:
    from mpyq import MPQArchive
except ImportError:
    MPQArchive = object

MAX_MEMBER = 4 * 1024 * 1024


class LimitedReader:
    def __init__(self, stream):
        self.stream = stream
        stream.seek(0, 2)
        self.size = stream.tell()
        stream.seek(0)

    def seek(self, offset, whence=0):
        position = self.stream.seek(offset, whence)
        if not 0 <= position <= self.size:
            raise ValueError('MPQ offset is outside the archive.')
        return position

    def read(self, size):
        if not 0 <= size <= MAX_MEMBER * 2:
            raise ValueError('MPQ read exceeds the scanner limit.')
        data = self.stream.read(size)
        if len(data) != size:
            raise ValueError('Truncated MPQ data.')
        return data


def decompress(data, size):
    if not data:
        raise ValueError('Empty compressed MPQ sector.')
    try:
        if data[0] == 2:
            decoder = zlib.decompressobj()
            result = decoder.decompress(data[1:], size + 1)
        elif data[0] == 16:
            decoder = bz2.BZ2Decompressor()
            result = decoder.decompress(data[1:], max_length=size + 1)
        else:
            raise ValueError(f'Unsupported MPQ compression: {data[0]}.')
    except (zlib.error, OSError, EOFError) as error:
        raise ValueError('Invalid compressed MPQ data.') from error
    if len(result) != size or not decoder.eof or decoder.unused_data:
        raise ValueError('MPQ decompressed size does not match its record.')
    return result


class ScriptArchive(MPQArchive):
    def __init__(self, stream):
        if MPQArchive is object:
            raise ValueError('The MPQ reader is unavailable. Install the launcher dependencies using the '
                             'virtual environment instructions in README.md, then rescan.')
        super().__init__(LimitedReader(stream), listfile=False)

    def read_header(self):
        header = super().read_header()
        if (header['format_version'] not in (0, 1, 2, 3) or header['sector_size_shift'] > 16
                or any(header[f'{kind}_table_entries'] > 65536 for kind in ('hash', 'block'))):
            raise ValueError('Unsupported MPQ header or oversized tables.')
        if header['format_version'] >= 1:
            self.file.seek(header['offset'] + 32)
            extended, hash_high, block_high = struct.unpack('<QHH', self.file.read(12))
            if extended or hash_high or block_high:
                raise ValueError('Extended MPQ offsets are not supported by the scanner.')
        if header['format_version'] == 3:
            self.file.seek(header['offset'] + 68)
            hash_size, block_size = struct.unpack('<QQ', self.file.read(16))
            if (hash_size != header['hash_table_entries'] * 16
                    or block_size != header['block_table_entries'] * 16):
                raise ValueError('Compressed MPQ lookup tables are not supported by the scanner.')
        if any(header.get(key, 0) for key in ('extended_block_table_offset', 'hash_table_offset_high',
                                             'block_table_offset_high')):
            raise ValueError('Extended MPQ offsets are not supported by the scanner.')
        return header

    def read_file(self, name):
        entry = self.get_hash_table_entry(name.encode('utf-8') if isinstance(name, str) else name)
        if entry is None:
            return None
        if entry.block_table_index >= len(self.block_table):
            raise ValueError('Invalid MPQ block index.')
        block = self.block_table[entry.block_table_index]
        if not block.flags & 0x80000000:
            return None
        if block.size > MAX_MEMBER or block.archived_size > MAX_MEMBER * 2:
            raise ValueError('Script member exceeds the 4 MiB scanner limit.')
        if block.flags & (0x10000 | 0x100 | 0x100000 | 0x2000000):
            raise ValueError('Encrypted, imploded, patched, or deleted MPQ members are unsupported.')
        self.file.seek(self.header['offset'] + block.offset)
        data = self.file.read(block.archived_size)
        if not block.size:
            return b''
        compressed = bool(block.flags & 0x200)
        if block.flags & 0x1000000 or not compressed:
            if compressed and len(data) < block.size:
                return decompress(data, block.size)
            if len(data) != block.size:
                raise ValueError('MPQ member size mismatch.')
            return data
        sector_size = 512 << self.header['sector_size_shift']
        sectors = (block.size + sector_size - 1) // sector_size
        count = sectors + 1 + bool(block.flags & 0x4000000)
        if len(data) < count * 4:
            raise ValueError('Truncated MPQ sector table.')
        positions = struct.unpack(f'<{count}I', data[:count * 4])
        if (positions[0] < count * 4 or positions[-1] > len(data)
                or any(a > b for a, b in zip(positions, positions[1:], strict=False))):
            raise ValueError('Invalid MPQ sector offsets.')
        parts = []
        for index in range(sectors):
            sector = data[positions[index]:positions[index + 1]]
            size = min(sector_size, block.size - index * sector_size)
            if len(sector) < size:
                sector = decompress(sector, size)
            if len(sector) != size:
                raise ValueError('MPQ sector size mismatch.')
            parts.append(sector)
        return b''.join(parts)
