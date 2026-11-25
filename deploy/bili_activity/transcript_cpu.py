"""Process one bounded video or audio window in an isolated CPU subprocess."""

import importlib.metadata
import json
import math
import re
import subprocess
import sys
import unicodedata
import wave
from pathlib import Path

import numpy as np


FPS = 2
WIDTH, HEIGHT = 960, 540
RATE = 16000


def frames(executable, path, start, end):
    command = [executable, "-nostdin", "-loglevel", "error", "-threads", "1", "-ss", str(start),
               "-protocol_whitelist", "file,pipe", "-i", str(path), "-t", str(end - start), "-an",
               "-filter_threads", "1", "-vf",
               f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2",
               "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = WIDTH * HEIGHT * 3
    try:
        for index in range(math.ceil((end - start) * FPS)):
            raw = process.stdout.read(size)
            if len(raw) != size:
                break
            yield start + index / FPS, np.frombuffer(raw, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        process.wait()


def text_rows(ocr, image):
    result = ocr(image, use_cls=False)
    if result.boxes is None or not result.txts:
        return []
    return [{"rect": (int(box[:, 0].min()), int(box[:, 1].min()),
                      int(box[:, 0].max()), int(box[:, 1].max())),
             "text": text.strip(), "score": float(score)}
            for box, text, score in zip(result.boxes, result.txts, result.scores) if text.strip()]


def locate(samples):
    """Pick a horizontal caption band that changes text across full frames."""
    candidates = {}
    for frame, rows in enumerate(samples):
        for row in rows:
            x1, y1, x2, y2 = row["rect"]
            height, center = y2 - y1, (y1 + y2) // 2
            if not (10 <= height <= 100 and x2 - x1 >= height * 2 and x1 < WIDTH * .7 and x2 > WIDTH * .3):
                continue
            for band in range(max(0, (center - 15) // 20), (center + 15) // 20 + 1):
                candidates.setdefault(band, []).append((frame, row))
    def rank(group):
        return (len({row["text"] for _, row in group}), len({frame for frame, _ in group}),
                max(row["rect"][3] for _, row in group))
    group = max(candidates.values(), key=rank, default=[])
    if len({row["text"] for _, row in group}) < 2:
        return None
    top = max(0, min(row["rect"][1] for _, row in group) - 16)
    bottom = min(HEIGHT, max(row["rect"][3] for _, row in group) + 18)
    return top, bottom


def merge_caption(rows, start, end, text, score):
    if not text:
        return
    normal = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))
    if rows and start <= rows[-1]["end"] + .05 and normal(rows[-1]["text"]) == normal(text):
        rows[-1]["end"] = end
        if score > rows[-1]["confidence"]:
            rows[-1]["text"], rows[-1]["confidence"] = text, score
    else:
        rows.append({"start": start, "end": end, "text": text, "confidence": score})


def join_caption_lines(boxes):
    groups = []
    for box in sorted(boxes, key=lambda item: item["rect"][1] + item["rect"][3]):
        _, top, _, bottom = box["rect"]
        center = (top + bottom) / 2
        if not groups or center - groups[-1][0] > max(5, (bottom - top) / 2):
            groups.append((center, []))
        groups[-1][1].append(box)
    lines = []
    for _, group in groups:
        text = ""
        for box in sorted(group, key=lambda item: item["rect"][0]):
            part = box["text"]
            if text and text[-1].isascii() and part[0].isascii() and part[0].isalnum():
                text += " "
            text += part
        lines.append(text)
    return "\n".join(lines)


def caption_window(executable, path, start, end, mode):
    try:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    except ImportError:
        raise RuntimeError("Install the local RapidOCR/ONNX media runtime") from None
    lang = {"auto": LangRec.CH, "zh": LangRec.CH, "en": LangRec.EN, "ja": LangRec.CH,
            "ko": LangRec.KOREAN}.get(mode)
    if lang is None:
        raise ValueError("unsupported OCR recognition language")
    engine = RapidOCR(params={"Global.use_cls": False, "Det.ocr_version": OCRVersion.PPOCRV5,
                              "Det.model_type": ModelType.MOBILE, "Rec.ocr_version": OCRVersion.PPOCRV5,
                              "Rec.model_type": ModelType.MOBILE, "Rec.lang_type": lang,
                              "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                              "EngineConfig.onnxruntime.inter_op_num_threads": 1})
    rows, count, last = [], 0, start
    stream = iter(frames(executable, path, start, end))
    last_crop, last_ocr, last_ocr_at = None, [], start

    def consume(at, image, band):
        nonlocal count, last, last_crop, last_ocr, last_ocr_at
        top, bottom = band
        crop = image[top:bottom]
        if at - last_ocr_at >= 1.5 or not np.array_equal(crop, last_crop):
            observed = text_rows(engine, crop)
            last_ocr = [row for row in observed
                        if row["rect"][3] - row["rect"][1] >= 9
                        and row["rect"][2] > WIDTH * .2 and row["rect"][0] < WIDTH * .8]
            last_crop, last_ocr_at = crop, at
        if last_ocr:
            merge_caption(rows, at, min(end, at + 1 / FPS), join_caption_lines(last_ocr),
                          round(min(p["score"] for p in last_ocr), 3))
        count += 1
        last = min(end, at + 1 / FPS)
        return bool(last_ocr)

    # Only a few full frames are buffered to locate each new layout.
    from itertools import islice

    band = None
    missing = 0
    while block := list(islice(stream, 4 if band is None or missing >= 6 else 1)):
        if band is None or missing >= 6:
            position = locate([text_rows(engine, frame) for _, frame in block])
            band = position or band or (int(HEIGHT * .64), HEIGHT)
            last_crop = None
            missing = 0
        for at, frame in block:
            missing = 0 if consume(at, frame, band) else missing + 1
    return {"lines": rows, "sampled_frames": count, "observed_end": last, "interval": 1 / FPS}


def vad_spans(probabilities, length):
    """Skip long probable silences while keeping short utterances and context."""
    seconds = .032
    regions = []
    for index, value in enumerate(probabilities):
        if value < .30:
            continue
        left = max(0, index * seconds - .24)
        right = min(length, (index + 1) * seconds + .24)
        if regions and left - regions[-1][1] < 1.0:
            regions[-1][1] = right
        else:
            regions.append([left, right])
    return regions


def speech_window(executable, path, start, end, folder):
    import onnxruntime as ort

    model = importlib.metadata.distribution("silero-vad").locate_file("silero_vad/data/silero_vad.onnx")
    options = ort.SessionOptions()
    options.inter_op_num_threads = options.intra_op_num_threads = 1
    detector = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
    command = [executable, "-nostdin", "-loglevel", "error", "-threads", "1", "-ss", str(start),
               "-protocol_whitelist", "file,pipe", "-i", str(path), "-t", str(end - start),
               "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"]
    pcm = subprocess.run(command, capture_output=True, timeout=120, check=True).stdout
    samples = np.frombuffer(pcm, dtype="<i2")
    length = len(samples) / RATE
    context = np.zeros(64, dtype=np.float32)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    probs = []
    for index in range(0, len(samples), 512):
        part = samples[index:index + 512].astype(np.float32) / 32768
        if len(part) < 512:
            part = np.pad(part, (0, 512 - len(part)))
        data = np.concatenate((context, part))
        probability, state = detector.run(None, {"input": data[None, :], "state": state, "sr": np.array(RATE, dtype=np.int64)})
        probs.append(float(probability.reshape(-1)[0]))
        context = data[-64:]
    clips = []
    for i, (left, right) in enumerate(vad_spans(probs, length)):
        name = f"speech-{i}.wav"
        with wave.open(str(folder / name), "wb") as output:
            output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            output.writeframes(samples[round(left * RATE):round(right * RATE)].tobytes())
        clips.append({"path": name, "start": round(start + left, 3), "end": round(start + right, 3)})
    return {"clips": clips, "observed_end": round(start + length, 3)}


if __name__ == "__main__":
    mode, executable, filename, start, end, option = sys.argv[1:]
    args = executable, Path(filename), float(start), float(end)
    result = caption_window(*args, option) if mode == "ocr" else speech_window(*args, Path(option))
    print("FISHHOME_RESULT " + json.dumps(result, ensure_ascii=True))
