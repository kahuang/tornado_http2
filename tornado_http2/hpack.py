import collections
import os
from tornado.escape import utf8

from .encoding import BitDecoder, EODError

class HpackDecoder(object):
    def __init__(self):
        self._header_table = collections.deque()
        self._header_table_size = 0
        # entries in the reference set are counted from the end of the
        # header table.
        self._reference_set = set()

    def decode(self, data):
        header_set = []
        bit_decoder = BitDecoder(data)
        emitted_refs = set()
        while not bit_decoder.eod():
            is_indexed = bit_decoder.read_bit()
            if is_indexed:
                idx = bit_decoder.read_hpack_int()
                ref_idx = len(self._header_table) - idx
                if ref_idx in self._reference_set:
                    self._reference_set.discard(ref_idx)
                    continue
                is_static, name, value = self.read_from_index(idx)
                header_set.append((name, value))
                if is_static:
                    self.add_to_header_table(name, value)
                    ref_idx = len(self._header_table)
                self._reference_set.add(ref_idx)
                emitted_refs.add(ref_idx)
            else:
                add_to_index = bit_decoder.read_bit()
                if add_to_index:
                    name, value = self.read_name_value_pair(bit_decoder)
                    header_set.append((name, value))
                    self.add_to_header_table(name, value)
                    ref_idx = len(self._header_table)
                    self._reference_set.add(ref_idx)
                    emitted_refs.add(ref_idx)
                else:
                    is_context_update = bit_decoder.read_bit()
                    if is_context_update:
                        clear_ref_set = bit_decoder.read_bit()
                        new_limit = bit_decoder.read_hpack_int()
                        if clear_ref_set:
                            if new_limit != 0:
                                raise ValueError(
                                    "bits after clear_ref_set must be zero")
                            self._reference_set.clear()
                        else:
                            raise NotImplementedError()
                    else:
                        # read the never-index bit and discard for now.
                        bit_decoder.read_bit()
                        header_set.append(self.read_name_value_pair(bit_decoder))
        for ref_idx in self._reference_set - emitted_refs:
            idx = len(self._header_table) - ref_idx + 1
            _, name, value = self.read_from_index(idx)
            header_set.append((name, value))
        return header_set

    def read_name_value_pair(self, bit_decoder):
        name_index = bit_decoder.read_hpack_int()
        if name_index == 0:
            name = self.read_string(bit_decoder)
        else:
            name = self.read_from_index(name_index)[1]
        value = self.read_string(bit_decoder)
        return name, value

    def read_string(self, bit_decoder):
        is_huffman = bit_decoder.read_bit()
        length = bit_decoder.read_hpack_int()
        if is_huffman:
            # read huffman chars until we have read 'length' bytes
            dest_byte = bit_decoder._byte_offset + length
            chars = []
            while bit_decoder._byte_offset < dest_byte:
                try:
                    chars.append(bit_decoder.read_huffman_char())
                except EODError:
                    # TODO: fix handling of EOS char.
                    break
            while bit_decoder._bit_offset != 0:
                pad_bit = bit_decoder.read_bit()
                if not pad_bit:
                    raise ValueError("padding bits must be 1")
        else:
            chars = [bit_decoder.read_char() for i in range(length)]
        return bytes(bytearray(chars))

    def read_from_index(self, idx):
        if idx <= len(self._header_table):
            return (False,) + self._header_table[idx - 1]
        else:
            return (True,) + _static_table[idx - len(self._header_table)]

    def add_to_header_table(self, name, value):
        self._header_table.appendleft((name, value))
        self._header_table_size += len(name) + len(value) + 32

def _load_static_table():
    """Parses the hpack static table, which was copied from
    http://http2.github.io/http2-spec/compression.html#static.table
    corresponding to
    http://tools.ietf.org/html/draft-ietf-httpbis-header-compression-12#appendix-A
    """
    # start the table with a dummy entry 0
    table = [None]
    with open(os.path.join(os.path.dirname(__file__),
                           'hpack_static_table.txt')) as f:
        for line in f:
            if not line:
                continue
            fields = line.split('\t')
            if int(fields[0]) != len(table):
                raise ValueError("inconsistent numbering in static table")
            name = utf8(fields[1].strip())
            value = utf8(fields[2].strip()) if len(fields) > 2 else None
            table.append((name, value))
    return table

_static_table = _load_static_table()