"""Probe WebM/Opus without trusting filename, MIME type, or a short magic check.

mutagen 不支持 WebM/Matroska，不能用来校验浏览器录音。本模块解析 EBML：
确认 DocType=webm、音频轨 CodecID=A_OPUS；时长优先用 Segment Info 的 Duration，
缺失或为 0 时用 Cluster/Block 时间戳，并补上最后一包 Opus TOC 帧长。
这是探测，不是转码。运行时不依赖 ffprobe。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

ID_EBML = 0x1A45DFA3
ID_DOC_TYPE = 0x4282
ID_SEGMENT = 0x18538067
ID_INFO = 0x1549A966
ID_TIMECODE_SCALE = 0x2AD7B1
ID_DURATION = 0x4489
ID_TRACKS = 0x1654AE6B
ID_TRACK_ENTRY = 0xAE
ID_TRACK_NUMBER = 0xD7
ID_TRACK_TYPE = 0x83
ID_CODEC_ID = 0x86
ID_CODEC_PRIVATE = 0x63A2
ID_CLUSTER = 0x1F43B675
ID_TIMESTAMP = 0xE7
ID_SIMPLE_BLOCK = 0xA3
ID_BLOCK_GROUP = 0xA0
ID_BLOCK = 0xA1

TRACK_TYPE_AUDIO = 2
OPUS_CODEC_ID = "A_OPUS"


class UnsupportedAudioFormat(ValueError):
    pass


@dataclass(frozen=True)
class WebmOpusProbe:
    duration_sec: float
    duration_source: str
    track_number: int


def probe_webm_opus(data: bytes) -> WebmOpusProbe:
    if len(data) < 4:
        raise UnsupportedAudioFormat("file too small")

    offset = 0
    elem_id, body_start, body_end, next_offset = _read_element(data, offset, len(data))
    if elem_id != ID_EBML:
        raise UnsupportedAudioFormat("not ebml")
    doc_type = _find_doc_type(data, body_start, body_end)
    if doc_type != "webm":
        raise UnsupportedAudioFormat("docType is not webm")

    offset = next_offset
    segment_found = False
    while offset < len(data):
        elem_id, body_start, body_end, next_offset = _read_element(data, offset, len(data))
        if elem_id == ID_SEGMENT:
            segment_found = True
            return _probe_segment(data, body_start, body_end)
        offset = next_offset

    if not segment_found:
        raise UnsupportedAudioFormat("missing segment")
    raise UnsupportedAudioFormat("invalid webm")


def _vint_width(first: int) -> int:
    for width in range(1, 9):
        if first & (0x80 >> (width - 1)):
            return width
    raise UnsupportedAudioFormat("invalid vint")


def _read_id(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise UnsupportedAudioFormat("truncated id")
    width = _vint_width(data[offset])
    end = offset + width
    if end > len(data):
        raise UnsupportedAudioFormat("truncated id")
    return int.from_bytes(data[offset:end], "big"), width


def _read_size(data: bytes, offset: int) -> tuple[int, int, bool]:
    if offset >= len(data):
        raise UnsupportedAudioFormat("truncated size")
    width = _vint_width(data[offset])
    end = offset + width
    if end > len(data):
        raise UnsupportedAudioFormat("truncated size")
    length_bit = 0x80 >> (width - 1)
    value = data[offset] & (length_bit - 1)
    for byte in data[offset + 1 : end]:
        value = (value << 8) | byte
    unknown = value == (1 << (7 * width)) - 1
    return value, width, unknown


def _read_element(data: bytes, offset: int, limit: int) -> tuple[int, int, int, int]:
    elem_id, id_width = _read_id(data, offset)
    size, size_width, unknown = _read_size(data, offset + id_width)
    body_start = offset + id_width + size_width
    if body_start > limit:
        raise UnsupportedAudioFormat("truncated element")
    if unknown:
        body_end = limit
        next_offset = limit
    else:
        body_end = body_start + size
        if body_end > limit:
            raise UnsupportedAudioFormat("truncated element")
        next_offset = body_end
    return elem_id, body_start, body_end, next_offset


def _iter_children(data: bytes, start: int, end: int):
    offset = start
    while offset < end:
        try:
            elem_id, body_start, body_end, next_offset = _read_element(data, offset, end)
        except UnsupportedAudioFormat:
            break
        if next_offset <= offset:
            break
        yield elem_id, body_start, body_end
        offset = next_offset


def _read_uint(body: bytes) -> int:
    if not body:
        return 0
    return int.from_bytes(body, "big")


def _read_string(body: bytes) -> str:
    return body.split(b"\x00", 1)[0].decode("ascii", errors="strict")


def _find_doc_type(data: bytes, start: int, end: int) -> str | None:
    for elem_id, body_start, body_end in _iter_children(data, start, end):
        if elem_id == ID_DOC_TYPE:
            try:
                return _read_string(data[body_start:body_end]).lower()
            except (UnicodeDecodeError, ValueError) as exc:
                raise UnsupportedAudioFormat("invalid docType") from exc
    return None


def _probe_segment(data: bytes, start: int, end: int) -> WebmOpusProbe:
    timecode_scale = 1_000_000
    info_duration_units: float | None = None
    opus_tracks: dict[int, bytes] = {}
    blocks: list[tuple[int, int, bytes]] = []

    for elem_id, body_start, body_end in _iter_children(data, start, end):
        if elem_id == ID_INFO:
            timecode_scale, info_duration_units = _parse_info(data, body_start, body_end)
        elif elem_id == ID_TRACKS:
            opus_tracks = _parse_tracks(data, body_start, body_end)
        elif elem_id == ID_CLUSTER:
            blocks.extend(_parse_cluster(data, body_start, body_end))

    if not opus_tracks:
        raise UnsupportedAudioFormat("no opus audio track")

    track_number = next(iter(opus_tracks))
    private = opus_tracks[track_number]
    if private and not private.startswith(b"OpusHead"):
        raise UnsupportedAudioFormat("opus codec private is invalid")

    opus_blocks = [item for item in blocks if item[0] in opus_tracks]
    if not opus_blocks:
        raise UnsupportedAudioFormat("no opus packets")

    timestamp_duration = _duration_from_blocks(opus_blocks, timecode_scale)
    info_duration = None
    if info_duration_units and info_duration_units > 0:
        info_duration = info_duration_units * timecode_scale / 1e9

    if info_duration and info_duration >= 0.5:
        return WebmOpusProbe(info_duration, "segment_info", track_number)
    if timestamp_duration > 0:
        return WebmOpusProbe(timestamp_duration, "cluster_timestamps", track_number)
    raise UnsupportedAudioFormat("duration unavailable")


def _parse_info(data: bytes, start: int, end: int) -> tuple[int, float | None]:
    scale = 1_000_000
    duration = None
    for elem_id, body_start, body_end in _iter_children(data, start, end):
        body = data[body_start:body_end]
        if elem_id == ID_TIMECODE_SCALE:
            scale = _read_uint(body) or scale
        elif elem_id == ID_DURATION:
            duration = _read_float(body)
    return scale, duration


def _read_float(body: bytes) -> float:
    if len(body) == 4:
        return struct.unpack(">f", body)[0]
    if len(body) == 8:
        return struct.unpack(">d", body)[0]
    raise UnsupportedAudioFormat("invalid duration float")


def _parse_tracks(data: bytes, start: int, end: int) -> dict[int, bytes]:
    opus_tracks: dict[int, bytes] = {}
    for elem_id, body_start, body_end in _iter_children(data, start, end):
        if elem_id != ID_TRACK_ENTRY:
            continue
        number = 1
        track_type = None
        codec_id = ""
        private = b""
        for child_id, child_start, child_end in _iter_children(data, body_start, body_end):
            body = data[child_start:child_end]
            if child_id == ID_TRACK_NUMBER:
                number = _read_uint(body)
            elif child_id == ID_TRACK_TYPE:
                track_type = _read_uint(body)
            elif child_id == ID_CODEC_ID:
                try:
                    codec_id = _read_string(body)
                except UnicodeDecodeError as exc:
                    raise UnsupportedAudioFormat("invalid codec id") from exc
            elif child_id == ID_CODEC_PRIVATE:
                private = body
        if track_type == TRACK_TYPE_AUDIO and codec_id == OPUS_CODEC_ID:
            opus_tracks[number] = private
    return opus_tracks


def _parse_cluster(data: bytes, start: int, end: int) -> list[tuple[int, int, bytes]]:
    cluster_time = 0
    blocks: list[tuple[int, int, bytes]] = []
    for elem_id, body_start, body_end in _iter_children(data, start, end):
        if elem_id == ID_TIMESTAMP:
            cluster_time = _read_uint(data[body_start:body_end])
        elif elem_id == ID_SIMPLE_BLOCK:
            parsed = _parse_block(data[body_start:body_end], cluster_time)
            if parsed:
                blocks.append(parsed)
        elif elem_id == ID_BLOCK_GROUP:
            for child_id, child_start, child_end in _iter_children(data, body_start, body_end):
                if child_id == ID_BLOCK:
                    parsed = _parse_block(data[child_start:child_end], cluster_time)
                    if parsed:
                        blocks.append(parsed)
    return blocks


def _parse_block(body: bytes, cluster_time: int) -> tuple[int, int, bytes] | None:
    if len(body) < 4:
        return None
    try:
        track, track_width, _unknown = _read_size(body, 0)
    except UnsupportedAudioFormat:
        return None
    offset = track_width
    if offset + 3 > len(body):
        return None
    relative = int.from_bytes(body[offset : offset + 2], "big", signed=True)
    flags = body[offset + 2]
    frames = body[offset + 3 :]
    packet = _first_laced_frame(frames, flags)
    if packet is None:
        return None
    return track, cluster_time + relative, packet


def _first_laced_frame(frames: bytes, flags: int) -> bytes | None:
    lace = (flags & 0x06) >> 1
    if lace == 0:
        return frames or None
    if not frames:
        return None
    if lace == 1:
        # Xiph: skip frame count and sizes, last frame is remainder.
        count = frames[0] + 1
        offset = 1
        sizes: list[int] = []
        for _ in range(count - 1):
            size = 0
            while offset < len(frames) and frames[offset] == 255:
                size += 255
                offset += 1
            if offset >= len(frames):
                return None
            size += frames[offset]
            offset += 1
            sizes.append(size)
        if offset > len(frames):
            return None
        return frames[offset : offset + sizes[0]] if sizes else frames[offset:] or None
    if lace == 2:
        count = frames[0] + 1
        rest = frames[1:]
        if count <= 0 or len(rest) % count != 0:
            return rest[: max(1, len(rest) // max(count, 1))] or None
        size = len(rest) // count
        return rest[:size] or None
    # EBML lacing
    count = frames[0] + 1
    offset = 1
    try:
        size, width, _ = _read_size(frames, offset)
    except UnsupportedAudioFormat:
        return None
    offset += width
    return frames[offset : offset + size] or None


def _duration_from_blocks(blocks: list[tuple[int, int, bytes]], timecode_scale: int) -> float:
    first_tick = blocks[0][1]
    last_tick = blocks[-1][1]
    last_packet = blocks[-1][2]
    span_sec = max(0, last_tick - first_tick) * timecode_scale / 1e9
    return span_sec + opus_packet_duration_sec(last_packet)


def opus_packet_duration_sec(packet: bytes) -> float:
    if not packet:
        return 0.0
    toc = packet[0]
    config = toc >> 3
    if config < 12:
        frame_ms = (10, 20, 40, 60)[config % 4]
    elif config < 16:
        frame_ms = (10, 20)[config % 2]
    else:
        frame_ms = (2.5, 5, 10, 20)[config % 4]
    code = toc & 3
    if code == 0:
        frames = 1
    elif code in (1, 2):
        frames = 2
    else:
        if len(packet) < 2:
            return 0.0
        frames = packet[1] & 0x3F
    return frames * frame_ms / 1000.0
