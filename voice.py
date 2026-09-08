#!/usr/bin/env python3
"""
Tự động tổng hợp giọng đọc TTS từ file metadata.csv bằng CapCut API.

Tính năng:
1. Cấu hình chọn giọng đọc linh hoạt bằng:
   - ID số (Resource ID, ví dụ: 7252594014782755330)
   - ID chữ (Voice Type, ví dụ: BV421_vivn_streaming)
   - Tên tiếng Việt (Ví dụ: "Nhỏ Ngọt Ngào", "Ban Mai")
   - Số thứ tự 1-10 (Ví dụ: 1, 9)
2. Đọc metadata.csv để tạo audio chuẩn tên file và lưu vào thư mục voice/
3. Hỗ trợ batch (gửi nhiều câu cùng lúc trong 1 request API)
4. Hỗ trợ resume: tự quét các file đã tải trong voice/ để chạy tiếp từ câu chưa tạo
5. Tự động kiểm soát tốc độ (delay) và thử lại (retry) tránh rate limit
"""

import argparse
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import wave

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from capcut_tts_api import CapCutClient, CapCutError

# ==============================================================================
# DANH SÁCH GIỌNG ĐỌC TIẾNG VIỆT ĐƯỢC HỖ TRỢ
# ==============================================================================
SUPPORTED_VOICES = [
    {
        "index": "1",
        "name": "Nhỏ Ngọt Ngào",
        "voice_type": "BV421_vivn_streaming",
        "resource_id": "7252594014782755330",
    },
    {
        "index": "2",
        "name": "Giọng Nữ Phổ Thông",
        "voice_type": "vi_female_huong",
        "resource_id": "7264854897953083905",
    },
    {
        "index": "3",
        "name": "Giọng Bé",
        "voice_type": "BV074_streaming_dsp",
        "resource_id": "7550087831092251920",
    },
    {
        "index": "4",
        "name": "Cô Gái Hoạt Ngôn",
        "voice_type": "BV074_streaming",
        "resource_id": "7102355709945188865",
    },
    {
        "index": "5",
        "name": "Hoai My",
        "voice_type": "vi-VN-HoaiMyNeural",
        "resource_id": "7371666434650280464",
    },
    {
        "index": "6",
        "name": "Nam Minh",
        "voice_type": "vi-VN-NamMinhNeural",
        "resource_id": "7371666524727153168",
    },
    {
        "index": "7",
        "name": "Việt Méo",
        "voice_type": "BV075_streaming_vibrato_dsp",
        "resource_id": "7569450639810465040",
    },
    {
        "index": "8",
        "name": "Mai",
        "voice_type": "BV562_streaming",
        "resource_id": "7483736254694035984",
    },
    {
        "index": "9",
        "name": "Ban Mai",
        "voice_type": "multi_female_yangguangnv_uranus_bigtts",
        "resource_id": "7637456432522218773",
    },
    {
        "index": "10",
        "name": "Review Phim new",
        "voice_type": "multi_female_richgirl_uranus_bigtts",
        "resource_id": "7637460351541447956",
    },
]

# Cấu hình mặc định (có thể điền ID số, ID chữ, hoặc Tên)
DEFAULT_VOICE = "BV074_streaming"  # 1 Hoặc "7637456432522218773", "Ban Mai", "9"
DEFAULT_BATCH_SIZE = 25           # Số câu trong 1 lần gọi API (khuyến nghị: 5-10, tránh CapCut server quá tải)
DEFAULT_AUDIO_FORMAT = "wav"       # Định dạng âm thanh: "wav" (16-bit 24kHz PCM không nén) hoặc "mp3"
DEFAULT_METADATA_PATH = "metadata.csv"
DEFAULT_OUTPUT_DIR = "HoatNgon"    # Thư mục lưu file audio
DEFAULT_RATE = "1.0"              # Tốc độ đọc
DEFAULT_DELAY = 1.0               # Thời gian nghỉ (giây) giữa các batch
DEFAULT_THREADS = 4               # Số luồng chạy đồng thời (1 đến 5 luồng)
DEFAULT_TIMEOUT = 90.0            # Thời gian chờ tối đa (giây) cho mỗi task TTS
MAX_RETRIES = 3                   # Số lần thử lại tối đa cho mỗi batch nếu gặp lỗi



def print_voice_table() -> None:
    """In bảng danh sách các giọng đọc hỗ trợ."""
    print("\n=== DANH SÁCH GIỌNG ĐỌC HỖ TRỢ ===")
    print(f"{'STT':<4} | {'Tên hiển thị':<22} | {'ID chữ (Voice Type)':<40} | {'ID số (Resource ID)':<20}")
    print("-" * 92)
    for v in SUPPORTED_VOICES:
        print(f"{v['index']:<4} | {v['name']:<22} | {v['voice_type']:<40} | {v['resource_id']:<20}")
    print("\n* Bạn có thể chọn giọng bằng: STT (1-10), ID chữ, ID số hoặc Tên hiển thị.")


def resolve_voice_info(voice_input: str, client: CapCutClient) -> Tuple[str, str, str]:
    """
    Nhận diện giọng đọc từ input (STT, ID số, ID chữ, hoặc Tên).
    Trả về tuple: (voice_type, resource_id, display_name)
    """
    raw = (voice_input or "").strip()
    target_lower = raw.lower()

    # 1. Tìm trong bảng SUPPORTED_VOICES
    for v in SUPPORTED_VOICES:
        if (
            v["index"] == raw
            or v["resource_id"] == raw
            or v["voice_type"].lower() == target_lower
            or v["name"].lower() == target_lower
        ):
            return v["voice_type"], v["resource_id"], v["name"]

    # 2. Tra cứu rộng hơn từ Voice.json qua SDK client
    resolved_type, resolved_res = client.resolve_voice(voice=raw)
    all_voices = client.list_voices()
    matched_name = raw
    for v in all_voices:
        if v.resource_id == resolved_res or v.voice_type.lower() == resolved_type.lower():
            matched_name = v.display_name
            break

    return resolved_type, resolved_res, matched_name


def normalize_text_for_capcut(text: str) -> str:
    """
    Chuẩn hóa tự động các từ dễ bị CapCut hiểu nhầm thành số La Mã hoặc bị lỗi đọc:
    - vi -> vy (tránh bị hiểu là số La Mã VI = 6 'sáu')
    - xi -> xy (tránh bị hiểu là số La Mã XI = 11 'mươi mốt')
    - a-xít -> axit (tránh bị đọc thành 'a đến X')
    - mi-li-lít -> mi li lít (tránh bị đọc thành 'mi đến ly đến lít')
    - mô-đem -> mô đem (tránh bị đọc thành 'mô đến đêm')
    """
    text = re.sub(r"\bvi\b", "vy", text)
    text = re.sub(r"\bVi\b", "Vy", text)
    text = re.sub(r"\bxi\b", "xy", text)
    text = re.sub(r"\bXi\b", "Xy", text)
    text = text.replace("a-xít", "axit").replace("A-xít", "Axit")
    text = text.replace("mi-li-lít", "mi li lít").replace("Mi-li-lít", "Mi li lít")
    text = text.replace("mô-đem", "mô đem").replace("Mô-đem", "Mô đem")
    return text


def load_metadata(file_path: Path, audio_format: str = DEFAULT_AUDIO_FORMAT) -> List[Dict[str, str]]:
    """
    Đọc file metadata.csv theo định dạng: <filename>|<text>
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file metadata: {file_path}")

    ext = f".{audio_format.lower().lstrip('.')}"
    items = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 1)
            if len(parts) != 2:
                print(f"[Cảnh báo] Dòng {line_num} không đúng định dạng: {line}", file=sys.stderr)
                continue
            raw_filename, text = parts[0].strip(), parts[1].strip()
            text = normalize_text_for_capcut(text)
            stem = Path(raw_filename).stem
            items.append({
                "raw_filename": raw_filename,
                "stem": stem,
                "text": text,
                "target_filename": f"{stem}{ext}",
            })
    return items


def is_valid_audio(file_path: Path, audio_format: str = "wav") -> bool:
    """
    Kiểm tra file audio có tồn tại, dung lượng hợp lệ và mở được không.
    """
    if not file_path.is_file() or file_path.stat().st_size == 0:
        return False
    if audio_format.lower() == "wav":
        if file_path.stat().st_size < 44:
            return False
        try:
            with wave.open(str(file_path), "rb") as wf:
                return wf.getnframes() > 0 and wf.getframerate() > 0
        except Exception:
            return False
    elif audio_format.lower() == "mp3":
        return file_path.stat().st_size > 256
    return True


def scan_existing_stems(output_dir: Path, audio_format: str = "wav") -> Set[str]:
    """
    Quét thư mục lưu audio để lấy danh sách các file đã được tạo thành công và không lỗi.
    Tự động dọn dẹp file tạm (.tmp_) và file lỗi.
    """
    if not output_dir.exists():
        return set()

    existing = set()
    for file in output_dir.iterdir():
        # Xóa file tạm sót lại từ lần chạy bị ngắt
        if file.name.startswith(".tmp_"):
            try:
                file.unlink()
            except OSError:
                pass
            continue

        if file.is_file():
            if is_valid_audio(file, audio_format=audio_format):
                existing.add(file.stem)
            else:
                # File rác hoặc lỗi -> xoá bỏ để tạo lại
                try:
                    file.unlink()
                except OSError:
                    pass
    return existing



def process_batch_with_retry(
    client: CapCutClient,
    batch: List[Dict[str, str]],
    voice_type: str,
    resource_id: str,
    rate: str,
    output_dir: Path,
    audio_format: str = "wav",
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    print_lock: Optional[threading.Lock] = None,
) -> bool:
    """
    Gửi 1 batch gồm nhiều câu lên API và tải về các file audio tương ứng.
    Ghi qua file tạm để đảm bảo tính nguyên vẹn tuyệt đối (chống file hỏng/rỗng khi dừng đột ngột).
    """
    batch_texts = [item["text"] for item in batch]

    for attempt in range(1, max_retries + 1):
        try:
            # Tính timeout linh hoạt (tối thiểu 60s, hoặc 4s/câu cho batch lớn)
            effective_timeout = max(timeout, len(batch) * 4.0)
            result = client.generate_speech(
                texts=batch_texts,
                voice=voice_type,
                resource_id=resource_id,
                rate=rate,
                audio_format=audio_format,
                wait=True,
                timeout=effective_timeout,
            )
            urls = client.extract_speech_urls(result)

            if len(urls) != len(batch):
                raise CapCutError(
                    f"Số lượng URL trả về ({len(urls)}) không khớp với số câu trong batch ({len(batch)})"
                )

            # Tải và lưu từng file audio (ghi nguyên tử qua file tạm)
            for item, url in zip(batch, urls):
                dest_file = output_dir / item["target_filename"]
                temp_file = dest_file.with_name(f".tmp_{item['target_filename']}")
                client.download_file(url, temp_file)
                if temp_file.exists():
                    temp_file.replace(dest_file)

            return True
        except Exception as exc:
            err_msg = f"   [Thử lại {attempt}/{max_retries}] Lỗi: {exc}"
            if print_lock:
                with print_lock:
                    print(err_msg, file=sys.stderr)
            else:
                print(err_msg, file=sys.stderr)

            if attempt < max_retries:
                time.sleep(2.0 * attempt)
            else:
                fail_msg = f"   [Thất bại] Bỏ qua batch sau {max_retries} lần thử lại."
                if print_lock:
                    with print_lock:
                        print(fail_msg, file=sys.stderr)
                else:
                    print(fail_msg, file=sys.stderr)
                return False
    return False


def verify_and_repair(
    client: CapCutClient,
    items: List[Dict[str, str]],
    output_dir: Path,
    voice_type: str,
    resource_id: str,
    rate: str,
    audio_format: str = "wav",
    timeout: float = DEFAULT_TIMEOUT,
    max_passes: int = 2,
) -> bool:
    """
    Kiểm tra nhanh toàn bộ danh sách file cần tạo.
    Nếu thiếu hoặc file bị lỗi/hỏng, tự động gọi API tạo lại.
    """
    total = len(items)
    print("\n--- KIỂM TRA TOÀN DIỆN DỮ LIỆU ĐẦU RA ---")
    print(f"Đang kiểm tra {total} file trong '{output_dir}'...")

    for pass_num in range(1, max_passes + 1):
        bad_items: List[Dict[str, str]] = []
        for item in items:
            dest = output_dir / item["target_filename"]
            if not is_valid_audio(dest, audio_format=audio_format):
                bad_items.append(item)
                if dest.exists():
                    try:
                        dest.unlink()
                    except OSError:
                        pass

        if not bad_items:
            print(f"[OK] 100% dữ liệu đạt chuẩn ({total}/{total} file {audio_format.upper()} hợp lệ, không lỗi).")
            return True

        print(f"[Cảnh báo] Phát hiện {len(bad_items)}/{total} file bị thiếu hoặc lỗi (Lần sửa {pass_num}/{max_passes}).")
        print("Đang tự động tạo lại các file này...")

        # Batch nhỏ (tối đa 5 câu) để sửa chắc chắn nhất, không sợ quá tải
        repair_batch_size = 5
        repair_batches = [
            bad_items[i : i + repair_batch_size]
            for i in range(0, len(bad_items), repair_batch_size)
        ]

        for b_idx, r_batch in enumerate(repair_batches, start=1):
            stems = [it["stem"] for it in r_batch]
            print(f"   -> Đang tạo lại batch {b_idx}/{len(repair_batches)} ({stems[0]} -> {stems[-1]})...", end=" ", flush=True)
            ok = process_batch_with_retry(
                client=client,
                batch=r_batch,
                voice_type=voice_type,
                resource_id=resource_id,
                rate=rate,
                output_dir=output_dir,
                audio_format=audio_format,
                timeout=timeout,
                max_retries=MAX_RETRIES,
            )
            print("OK" if ok else "LỖI")
            time.sleep(0.5)

    # Kiểm tra lần cuối cùng
    final_bad = [
        it for it in items
        if not is_valid_audio(output_dir / it["target_filename"], audio_format=audio_format)
    ]
    if final_bad:
        print(f"[Thất bại] Vẫn còn {len(final_bad)}/{total} file chưa thể tạo lại thành công.")
        return False

    print(f"[OK] Đã sửa xong toàn bộ! 100% dữ liệu đạt chuẩn ({total}/{total} file {audio_format.upper()}).")
    return True


def main():
    parser = argparse.ArgumentParser(description="Chuyển đổi metadata.csv thành audio TTS CapCut")
    parser.add_argument(
        "--voice",
        "--voice-id",
        dest="voice",
        default=DEFAULT_VOICE,
        help=f"ID số, ID chữ, STT (1-10) hoặc tên giọng đọc (Mặc định: {DEFAULT_VOICE})",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="In danh sách các giọng đọc hỗ trợ rồi thoát",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Số câu trong 1 lượt gọi API (Mặc định: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--metadata",
        default=DEFAULT_METADATA_PATH,
        help=f"Đường dẫn file metadata (Mặc định: {DEFAULT_METADATA_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Thư mục lưu audio đầu ra (Mặc định: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--format",
        choices=["wav", "mp3"],
        default=DEFAULT_AUDIO_FORMAT,
        help=f"Định dạng âm thanh đầu ra: wav (16-bit 24kHz PCM chất lượng cao) hoặc mp3 (Mặc định: {DEFAULT_AUDIO_FORMAT})",
    )
    parser.add_argument(
        "--rate",
        default=DEFAULT_RATE,
        help=f"Tốc độ đọc (Mặc định: {DEFAULT_RATE})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Thời gian nghỉ giữa các batch tính bằng giây (Mặc định: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--threads",
        "-t",
        type=int,
        default=DEFAULT_THREADS,
        help=f"Số luồng xử lý song song (min 1, max 5, mặc định: {DEFAULT_THREADS})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Thời gian chờ tối đa (giây) cho mỗi task API (Mặc định: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Giới hạn số câu cần tạo trong lần chạy này (tùy chọn, hữu ích khi test)",
    )

    args = parser.parse_args()

    if args.list_voices:
        print_voice_table()
        return

    # Giới hạn chặt chẽ: min 1 luồng, max 5 luồng
    threads = max(1, min(5, args.threads))

    metadata_path = Path(args.metadata)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=========================================================")
    print("CapCut TTS - Chuyển đổi hàng loạt từ metadata.csv")
    print("=========================================================")

    # 1. Khởi tạo client & nhận diện giọng đọc (hỗ trợ cả ID số, ID chữ, tên, STT)
    client = CapCutClient()
    voice_type, resource_id, display_name = resolve_voice_info(args.voice, client)
    print(f"Giọng đọc   : {display_name}")
    print(f"ID chữ      : {voice_type}")
    print(f"ID số       : {resource_id}")
    print(f"Cấu hình    : Batch={args.batch_size} câu/lần | Luồng={threads} | Delay={args.delay}s | Tốc độ={args.rate} | Định dạng={args.format.upper()}")

    # 2. Đọc metadata
    all_items = load_metadata(metadata_path, audio_format=args.format)
    target_items = all_items[: args.limit] if args.limit and args.limit > 0 else all_items
    total_items = len(target_items)
    print(f"Tổng số câu : {total_items} câu cần xử lý trong {metadata_path}")

    # 3. Quét các file đã có để resume (chỉ tính file không lỗi)
    existing_stems = scan_existing_stems(output_dir, audio_format=args.format)
    pending_items = [item for item in target_items if item["stem"] not in existing_stems]
    completed_count = total_items - len(pending_items)

    if completed_count > 0:
        print(f"Tiến độ cũ  : Đã có {completed_count}/{total_items} file hợp lệ trong '{output_dir}'.")
        if pending_items:
            print(f"Tiếp tục từ : '{pending_items[0]['stem']}' ({len(pending_items)} câu còn lại).")
    else:
        print(f"Tiến độ cũ  : Chưa có file nào trong '{output_dir}'. Bắt đầu từ câu đầu tiên: '{target_items[0]['stem']}'.")

    if not pending_items:
        print("\nTiến độ: Đã có đủ file audio. Bắt đầu kiểm tra chất lượng file...")
        verify_and_repair(
            client=client,
            items=target_items,
            output_dir=output_dir,
            voice_type=voice_type,
            resource_id=resource_id,
            rate=args.rate,
            audio_format=args.format,
            timeout=args.timeout,
        )
        return

    # 4. Chia batch không trùng lặp, không đè lên nhau
    batches = [
        pending_items[i : i + args.batch_size]
        for i in range(0, len(pending_items), args.batch_size)
    ]
    total_batches = len(batches)
    processed_count = completed_count

    print_lock = threading.Lock()
    failed_batches: List[int] = []

    mode_str = "tuần tự (1 luồng)" if threads == 1 else f"đa luồng ({threads} luồng song song)"
    print(f"\nBắt đầu xử lý {len(pending_items)} câu qua {total_batches} batches ({mode_str})...\n")

    start_time = time.time()

    def process_single_batch_task(b_idx: int, batch: List[Dict[str, str]]) -> bool:
        nonlocal processed_count
        # Mỗi luồng khởi tạo 1 client riêng biệt để đảm bảo an toàn tuyệt đối
        thread_client = CapCutClient()
        thread_name = threading.current_thread().name

        batch_stems = [it["stem"] for it in batch]
        first_stem = batch_stems[0]
        last_stem = batch_stems[-1]
        stem_range = first_stem if len(batch_stems) == 1 else f"{first_stem} -> {last_stem}"

        if threads == 1:
            with print_lock:
                print(
                    f"[Batch {b_idx}/{total_batches}] Đang tạo {len(batch)} câu ({stem_range})...",
                    end=" ",
                    flush=True,
                )

        success = process_batch_with_retry(
            client=thread_client,
            batch=batch,
            voice_type=voice_type,
            resource_id=resource_id,
            rate=args.rate,
            output_dir=output_dir,
            audio_format=args.format,
            timeout=args.timeout,
            max_retries=MAX_RETRIES,
            print_lock=print_lock,
        )

        with print_lock:
            if success:
                processed_count += len(batch)
                progress_pct = (processed_count / total_items) * 100
                if threads == 1:
                    print(f"OK ({processed_count}/{total_items} - {progress_pct:.1f}%)")
                else:
                    print(
                        f"[Batch {b_idx:02d}/{total_batches}] [{thread_name}] Tạo {len(batch)} câu ({stem_range}) -> OK ({processed_count}/{total_items} - {progress_pct:.1f}%)"
                    )
            else:
                failed_batches.append(b_idx)
                if threads == 1:
                    print("LỖI")
                else:
                    print(
                        f"[Batch {b_idx:02d}/{total_batches}] [{thread_name}] ({stem_range}) -> THẤT BẠI sau {MAX_RETRIES} lần thử lại!"
                    )

        if args.delay > 0:
            time.sleep(args.delay)

        return success

    try:
        if threads == 1:
            # Chạy tuần tự 1 luồng đơn giản, không khác gì trước đây
            for b_idx, batch in enumerate(batches, start=1):
                process_single_batch_task(b_idx, batch)
        else:
            # Chạy song song từ 2 đến 5 luồng
            with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="Luồng") as executor:
                future_to_idx = {}
                for b_idx, batch in enumerate(batches, start=1):
                    fut = executor.submit(process_single_batch_task, b_idx, batch)
                    future_to_idx[fut] = b_idx
                    # Giãn cách nhẹ khi khởi tạo để tránh spike cùng 1 tích tắc
                    if args.delay > 0:
                        time.sleep(min(args.delay / threads, 0.3))

                for future in as_completed(future_to_idx):
                    future.result()

    except KeyboardInterrupt:
        print("\n\n[Tạm dừng] Đã nhận tín hiệu dừng từ người dùng.")
        print(f"Tiến độ hiện tại: {processed_count}/{total_items} câu. Chạy lại script để tiếp tục.")
        sys.exit(0)

    elapsed = time.time() - start_time
    print("\n=========================================================")
    print(f"Tổng thời gian chạy đợt tạo: {elapsed:.2f} giây.")
    print("=========================================================")

    # 5. Bước kiểm tra toàn diện và tự động sửa file lỗi/thiếu
    verify_and_repair(
        client=client,
        items=target_items,
        output_dir=output_dir,
        voice_type=voice_type,
        resource_id=resource_id,
        rate=args.rate,
        audio_format=args.format,
        timeout=args.timeout,
    )



if __name__ == "__main__":
    main()
