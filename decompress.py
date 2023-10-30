# This file is Copyright 2022 Forensicxlab and under the GNU GPLv3 license
# Inspired by : https://raw.githubusercontent.com/Velocidex/go-prefetch/master/lzxpress.go
import struct
from typing import Tuple, List, Union

class BitStream:
    def __init__(self, source: bytes, in_pos: int):
        self.source = source
        self.index = in_pos + 4
        # read UInt16 little endian
        mask = struct.unpack_from('<H', source, in_pos)[0] << 16
        mask += struct.unpack_from('<H', source, in_pos + 2)[0]
        self.mask = mask
        self.bits = 32

    def lookup(self, n: int) -> int:
        if n == 0:
            return 0
        return self.mask >> (32 - n)
        
    def skip(self, n: int) -> Union[None, Exception]:
        self.mask = ((self.mask << n) & 0xFFFFFFFF)
        self.bits -= n
        if self.bits < 16:
            if self.index + 2 > len(self.source):
                return Exception("EOF Error")
            # read UInt16 little endian
            self.mask += ((struct.unpack_from('<H', self.source, self.index)[0]) << (16 - self.bits)) & 0xFFFFFFFF 
            self.index += 2
            self.bits += 16

        return None

    def __str__(self):
        return f"{self.id}: symbol {self.symbol} length {self.length}"
class PREFIX_CODE_NODE:
    def __init__(self):
        self.id = 0
        self.symbol = 0
        self.leaf = False
        self.child = [None, None]

    def __str__(self):
        return f"Node {self.id}: symbol {self.symbol} leaf {self.leaf}"

class PREFIX_CODE_SYMBOL:
    def __init__(self):
        self.id = 0
        self.symbol = 0
        self.length = 0

    def __str__(self):
        return f"Symbol {self.id}: symbol {self.symbol} length {self.length}"


def prefix_code_tree_add_leaf(treeNodes: List[PREFIX_CODE_NODE], leafIndex: int, mask: int, bits: int) -> int:
    node = treeNodes[0]
    i = leafIndex + 1
    childIndex = None

    while bits > 1:
        bits -= 1
        childIndex = (mask >> bits) & 1
        if node.child[childIndex] == None:
            node.child[childIndex] = treeNodes[i]
            treeNodes[i].leaf = False
            i += 1
        node = node.child[childIndex]

    node.child[mask&1] = treeNodes[leafIndex]

    return i

def prefix_code_tree_rebuild(input: bytes) -> PREFIX_CODE_NODE:
    treeNodes = [PREFIX_CODE_NODE() for _ in range(1024)]
    symbolInfo = [PREFIX_CODE_SYMBOL() for _ in range(512)]

    for i in range(256):
        value = input[i]

        symbolInfo[2*i].id = 2 * i
        symbolInfo[2*i].symbol = 2 * i
        symbolInfo[2*i].length = value & 0xf

        value >>= 4

        symbolInfo[2*i+1].id = 2*i + 1
        symbolInfo[2*i+1].symbol = 2*i + 1
        symbolInfo[2*i+1].length = value & 0xf

    symbolInfo = sorted(symbolInfo, key=lambda x: (x.length, x.symbol))

    i = 0
    while i < 512 and symbolInfo[i].length == 0:
        i += 1

    mask = 0
    bits = 1

    root = treeNodes[0]
    root.leaf = False

    j = 1
    while i < 512:
        treeNodes[j].id = j
        treeNodes[j].symbol = symbolInfo[i].symbol
        treeNodes[j].leaf = True
        mask = mask << (symbolInfo[i].length - bits)
        bits = symbolInfo[i].length
        j = prefix_code_tree_add_leaf(treeNodes, j, mask, bits)
        mask += 1
        i += 1

    return root

def prefix_code_tree_decode_symbol(bstr: BitStream, root: PREFIX_CODE_NODE) -> Tuple[int, Union[None, Exception]]:
    node = root
    i = 0
    while True:
        bit = bstr.lookup(1)
        err = bstr.skip(1)
        if err is not None:
            return 0, err

        node = node.child[bit]
        if node == None:
            return 0, Exception("Corruption detected")

        if node.leaf:
            break
    return node.symbol, None

def lz77_huffman_decompress_chunck(in_idx: int, 
                                   input: bytes, 
                                   out_idx: int, 
                                   output: bytearray, 
                                   chunk_size: int) -> Tuple[int, int, Union[None, Exception]]:
    
    # Ensure there are at least 256 bytes available to read
    if in_idx + 256 > len(input):
        return 0, 0, Exception("EOF Error")

    root = prefix_code_tree_rebuild(input[in_idx:])
    #print_tree(root)
    bstr = BitStream(input, in_idx+256)

    i = out_idx

    while i < out_idx + chunk_size:
        symbol, err = prefix_code_tree_decode_symbol(bstr, root)
        
        if err is not None:
            return int(bstr.index), i, err
        
        if symbol < 256:
            output[i] = symbol
            i += 1
        else:
            symbol -= 256
            length = symbol & 15
            symbol >>= 4

            offset = 0
            if symbol != 0:
                offset = int(bstr.lookup(symbol))

            offset |= 1 << symbol
            offset = -offset

            if length == 15:
                length = bstr.source[bstr.index] + 15
                bstr.index += 1
                
                if length == 270:
                    length = struct.unpack_from('<H', bstr.source, bstr.index)[0]
                    bstr.index += 2

            err = bstr.skip(symbol)
            if err is not None:
                return int(bstr.index), i, err
            
            length += 3
            while length > 0:
                if i + offset < 0:
                    print(i + offset)
                    return int(bstr.index), i, Exception("Decompression Error")
                
                output[i] = output[i + offset]
                i += 1
                length -= 1
                if length==0:
                    break
    return int(bstr.index), i, None


def lz77_huffman_decompress(input: bytes, output_size: int) -> Tuple[bytes, Union[None, Exception]]:
    output = bytearray(output_size)
    err = None

    # Index into the input buffer.
    in_idx = 0

    # Index into the output buffer.
    out_idx = 0

    while True:
        # How much data belongs in the current chunk. Chunks
        # are split into maximum 65536 bytes.
        chunk_size = output_size - out_idx
        if chunk_size > 65536:
            chunk_size = 65536

        in_idx, out_idx, err = lz77_huffman_decompress_chunck(
            in_idx, input, out_idx, output, chunk_size)
        if err is not None:
            return output, err
        if out_idx >= len(output) or in_idx >= len(input):
            break
    return output, None


with open("ATOM.EXE-3A9166E2.pf","rb") as stream:
    stream.seek(0x0004)
    decompressed_size = int.from_bytes(stream.read(4),"little")
    stream.seek(0x0008)
    compressed_bytes = stream.read()
out = lz77_huffman_decompress(bytearray(compressed_bytes),decompressed_size)[0]

