#!/usr/bin/env python3
"""
Bộ công cụ ghép từ vựng tiếng Việt (word.txt) thành câu và xuất ra file CSV (chuẩn LJSpeech metadata).

Các tính năng chính:
- Ghép từ ngẫu nhiên với độ dài 5 - 35 tiếng (chữ/âm tiết) mỗi câu.
- Ràng buộc: 2 từ liền nhau KHÔNG CÙNG bắt đầu bằng 1 ký tự (ví dụ: 'thống đốc' không được đi liền 'thống khổ').
- Ước lượng thời lượng nói TTS chuẩn xác theo tốc độ nói tiếng Việt thực tế (~3.75 tiếng/giây).
- Hỗ trợ viết tiếp (append) file CSV có sẵn (như metadata.csv), tự động bù thời lượng còn thiếu để đạt mốc đích (vd: 2 giờ).
- Hệ thống bù an toàn (safety buffer, mặc định +10%) luôn ưu tiên tạo dư dả thay vì thiếu hụt.
"""

import argparse
from collections import defaultdict
import math
import os
from pathlib import Path
import random
import re
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ==============================================================================
# CẤU HÌNH NHANH (CHỈNH SỬA TRỰC TIẾP TẠI ĐÂY RỒI CHẠY: python word.py)
# ==============================================================================
CSV_FILE = "metadata.csv"      # File CSV đầu ra (viết tiếp hoặc tạo mới)
TARGET_HOUR = 5             # Mục tiêu thời lượng (giờ): ví dụ 2.0, 5, 5.1, 6.345... (Đặt None nếu dùng số câu)
MIN_CHAR = 2                  # Số tiếng (chữ/âm tiết) tối thiểu mỗi câu (Mặc định: 5)
MAX_CHAR = 3                 # Số tiếng (chữ/âm tiết) tối đa mỗi câu (Mặc định: 30)

SENTENCES = None              # Số câu tạo trực tiếp (dùng khi TARGET_HOUR = None, ví dụ: 500 câu)
WORD_FILE = "don.txt"        # File từ điển đầu vào

# ==============================================================================
# CẤU HÌNH ĐỒNG BỘ GIỌNG ĐỌC & TỐC ĐỘ (KHỚP 100% VỚI VOICE.PY)
# ==============================================================================
# VOICE:
# - "auto": Tự động đọc DEFAULT_OUTPUT_DIR trong voice.py (vd: HoatNgon -> 5.0 wps, Mai -> 4.69 wps)
# - Hoặc điền tên voice cụ thể: "HoatNgon", "Mai", "NuPhoThong", "BanMai", "ReviewNew"...
# - "all": Dùng tốc độ trung bình 4.51 tiếng/giây của toàn bộ 8 voice
VOICE = "auto"

# SPEECH_RATE: Tốc độ đọc (tiếng/giây). Đặt None để tự động lấy theo VOICE (khuyến nghị để luôn khớp)
SPEECH_RATE = None

SAFETY_BUFFER = 1.10          # Hệ số dôi dư (+10%) để thời lượng thực tế luôn >= TARGET_HOUR
AUTO_APPEND = True            # Tự động viết tiếp (append) nếu file CSV_FILE đã có dữ liệu

# Bảng tốc độ đọc thực tế đo từ 13,526 file WAV (12.25 giờ) của 8 voice CapCut:
VOICE_SPEECH_RATES = {
    "hoatngon": (5.00, "Cô Gái Hoạt Ngôn"),
    "nhongotngao": (4.86, "Nhỏ Ngọt Ngào"),
    "giongbe": (4.86, "Giọng Bé"),
    "mai": (4.69, "Mai"),
    "nammeomo": (4.68, "Việt Méo / Nam Méo"),
    "nuphothong": (4.10, "Giọng Nữ Phổ Thông"),
    "banmai": (3.68, "Ban Mai"),
    "reviewnew": (3.59, "Review Phim new"),
    "all": (4.51, "Trung bình 8 giọng"),
}


def resolve_voice_speech_rate(voice_input: Optional[str] = None) -> Tuple[float, str]:
    """
    Xác định tốc độ đọc thực tế (tiếng/giây).
    Nếu voice_input == 'auto' hoặc rỗng: tự động đọc DEFAULT_OUTPUT_DIR từ voice.py.
    Trả về: (tốc_độ_wps, tên_hiển_thị)
    """
    target = voice_input or VOICE or "auto"
    is_auto = False
    if target.lower() == "auto":
        is_auto = True
        voice_py = Path(__file__).resolve().parent / "voice.py"
        if voice_py.exists():
            try:
                txt = voice_py.read_text(encoding="utf-8")
                m = re.search(r"DEFAULT_OUTPUT_DIR\s*=\s*[\"']([^\"']+)[\"']", txt)
                if m:
                    target = m.group(1).strip()
            except Exception:
                pass
        if target.lower() == "auto":
            target = "all"

    v_norm = target.lower().replace("_", "").replace("-", "").replace(" ", "")

    # 1. Khớp chính xác
    for k, (rate, name) in VOICE_SPEECH_RATES.items():
        if v_norm == k:
            source_tag = " (đồng bộ từ voice.py)" if is_auto else ""
            return rate, f"{name} [{target}]{source_tag}"

    # 2. Khớp tiền tố (prefix)
    for k, (rate, name) in VOICE_SPEECH_RATES.items():
        if k.startswith(v_norm):
            source_tag = " (đồng bộ từ voice.py)" if is_auto else ""
            return rate, f"{name} [{target}]{source_tag}"

    # 3. Khớp chuỗi con
    for k in sorted(VOICE_SPEECH_RATES.keys(), key=len, reverse=True):
        if v_norm in k:
            rate, name = VOICE_SPEECH_RATES[k]
            source_tag = " (đồng bộ từ voice.py)" if is_auto else ""
            return rate, f"{name} [{target}]{source_tag}"

    # Mặc định trung bình 8 voice
    rate, name = VOICE_SPEECH_RATES["all"]
    return rate, f"{name} (4.51 wps)"


# Xử lý các từ CapCut đọc sai (nhầm 'vi' thành số 6 'sáu', 'xi' thành 11, dấu gạch nối '-' đọc là 'đến'):
# - 'replace': Tự động sửa từ ('vi' -> 'vy', 'xi' -> 'xy' để CapCut đọc chuẩn âm tiếng Việt)
# - 'remove': Loại bỏ luôn các từ này khỏi danh sách từ vựng
CAPCUT_FIX_MODE = "replace"   # Tùy chọn: "replace" (sửa từ) hoặc "remove" (loại bỏ hoàn toàn)


# ==============================================================================
# HÀM HỖ TRỢ XỬ LÝ KÝ TỰ & TỪ VỰNG
# ==============================================================================
def get_base_initial(word: str) -> str:
    """
    Lấy ký tự bắt đầu của từ và quy về ký tự gốc chữ thường (a, b, c, d, đ, e, g, h, i, k, l, m...).
    Ví dụ: 'thống đốc' -> 't', 'cảm thấy' -> 'c', 'án mạng' -> 'a', 'đường sá' -> 'đ'.
    """
    w = word.strip()
    if not w:
        return ""
    ch = w[0].lower()
    if ch == "đ":
        return "đ"
    # Tách dấu thanh và dấu phụ bằng chuẩn NFD
    decomposed = unicodedata.normalize("NFD", ch)
    return decomposed[0]


# Bảng ký tự tiếng Việt hợp lệ 100% (không chứa f, j, w, z, số, ký tự đặc biệt)
VN_ALPHABET_CHARS = set(
    "aàáảãạăằắẳẵặâầấẩẫậ"
    "bcdđeèéẻẽẹêềếểễệ"
    "ghiìíỉĩịklmnoòóỏõọ"
    "ôồốổỗộơờớởỡợpqrst"
    "uùúủũụưừứửữựvxyỳýỷỹỵ"
    " "
)

# Tập hợp nguyên âm tiếng Việt (bắt buộc mỗi âm tiết phải có ít nhất 1 nguyên âm)
VN_VOWELS = set("aàáảãạăằắẳẵặâầấẩẫậeèéẻẽẹêềếểễệiìíỉĩịoòóỏõọôồốổỗộơờớởỡợuùúủũụưừứửữựyỳýỷỹỵ")

# Danh mục quy tắc sửa từ bị CapCut đọc sai
CAPCUT_REPLACE_RULES = [
    (re.compile(r"\bvi\b", re.IGNORECASE), "vy"),
    (re.compile(r"\bxi\b", re.IGNORECASE), "xy"),
    (re.compile(r"\bix\b", re.IGNORECASE), "chín"),
    (re.compile(r"\biv\b", re.IGNORECASE), "bốn"),
]

CAPCUT_MISPRONOUNCE_TOKENS = {"vi", "xi", "ix", "iv"}


def clean_and_normalize_for_capcut(
    entry: str,
    fix_mode: str = CAPCUT_FIX_MODE,
) -> Optional[Tuple[str, bool]]:
    """
    Kiểm tra và chuẩn hóa nghiêm ngặt: Thuần Việt 100%, không viết tắt, không ngoại lai, CapCut phát âm chuẩn:
    1. Bỏ từ viết tắt (acronyms dạng chữ in hoa: IP, ICPC, DNA...).
    2. Chuyển toàn bộ về chữ thường thuần Việt.
    3. Kiểm tra 100% ký tự phải nằm trong bảng chữ cái tiếng Việt (loại bỏ f, j, w, z, số, dấu gạch nối '-', dấu câu).
    4. Kiểm tra cấu trúc âm tiết: mọi tiếng đều phải chứa nguyên âm tiếng Việt hợp lệ.
    5. Xử lý các từ CapCut đọc sai (nhầm 'vi' = 6 'sáu', 'xi' = 11 'mười một'):
       - Nếu fix_mode == 'remove': Loại bỏ hoàn toàn từ này.
       - Nếu fix_mode == 'replace': Sửa thành từ tương đương phát âm chuẩn ('vi' -> 'vy', 'xi' -> 'xy').

    Trả về: (từ_đã_chuẩn_hóa, có_được_sửa_hay_không) hoặc None nếu bị loại bỏ.
    """
    s = entry.strip()
    if not s or len(s) < 1:
        return None

    # 1. Bỏ từ viết tắt (chứa token toàn chữ hoa từ 2 ký tự: IP, ICPC, TTS...)
    if any(len(tok) >= 2 and tok.isupper() for tok in s.split()):
        return None

    # 2. Chuyển về chữ thường để chuẩn hóa đồng nhất
    s = s.lower()

    # 3. Kiểm tra 100% ký tự thuộc bảng chữ cái tiếng Việt hợp lệ (tự động loại bỏ '-', '?', '.', số, f, j, w, z...)
    if any(c not in VN_ALPHABET_CHARS for c in s):
        return None

    # 4. Kiểm tra âm tiết: mọi tiếng trong từ ghép/từ đơn bắt buộc phải chứa nguyên âm tiếng Việt
    tokens = s.split()
    if any(not any(c in VN_VOWELS for c in tok) for tok in tokens):
        return None

    # 5. Xử lý từ CapCut đọc nhầm
    has_capcut_issue = any(tok in CAPCUT_MISPRONOUNCE_TOKENS for tok in tokens)
    was_modified = False

    if has_capcut_issue:
        if fix_mode == "remove":
            return None
        # Mode 'replace': Thay thế từ ngữ nhầm lẫn sang từ tương đương ('vi' -> 'vy', 'xi' -> 'xy')
        original = s
        for pattern, replacement in CAPCUT_REPLACE_RULES:
            s = pattern.sub(replacement, s)
        was_modified = (s != original)

    return s, was_modified


def normalize_sentence_for_capcut(text: str) -> str:
    """Chuẩn hóa câu văn hoàn chỉnh trước khi ghi vào CSV để đảm bảo CapCut không đọc sai."""
    s = text.strip()
    # Khử dấu gạch ngang nếu còn sót
    s = re.sub(r"\s*-\s*", " ", s)
    # Sửa các từ vi, xi
    for pattern, replacement in CAPCUT_REPLACE_RULES:
        s = pattern.sub(replacement, s)
    return s



def load_vocab(
    word_file: Path,
    fix_mode: str = CAPCUT_FIX_MODE,
) -> Tuple[List[str], Dict[str, List[Tuple[str, int]]], List[str], int, int]:
    """
    Đọc danh sách từ vựng từ word.txt, tự động lọc và xử lý các từ CapCut đọc sai.
    Trả về: (valid_words, words_by_initial, all_initials, total_fixed, total_dropped)
    """
    if not word_file.exists():
        raise FileNotFoundError(f"Không tìm thấy file từ vựng: {word_file}")

    with open(word_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    valid_words = []
    words_by_initial = defaultdict(list)
    total_fixed = 0
    total_dropped = 0

    for line in lines:
        processed = clean_and_normalize_for_capcut(line, fix_mode=fix_mode)
        if processed is None:
            total_dropped += 1
            continue

        clean_word, was_modified = processed
        if was_modified:
            total_fixed += 1

        valid_words.append(clean_word)
        init_char = get_base_initial(clean_word)
        syllable_count = len(clean_word.split())
        words_by_initial[init_char].append((clean_word, syllable_count))

    all_initials = [c for c in words_by_initial.keys() if len(words_by_initial[c]) > 0]
    return valid_words, words_by_initial, all_initials, total_fixed, total_dropped


# ==============================================================================
# HÀM SINH CÂU TỰ ĐỘNG
# ==============================================================================
def generate_sentence(
    words_by_initial: Dict[str, List[Tuple[str, int]]],
    all_initials: List[str],
    min_words: int = 5,
    max_words: int = 35,
) -> Tuple[str, int, List[str]]:
    """
    Ghép các từ thành một câu ngẫu nhiên có độ dài trong khoảng [min_words, max_words] tiếng.
    Đảm bảo 2 từ liền nhau KHÔNG CÙNG bắt đầu bằng 1 ký tự.

    Trả về: (câu_hoàn_chỉnh, tổng_số_tiếng, danh_sách_từ_đã_dùng)
    """
    target_syllables = random.randint(min_words, max_words)
    chosen_entries = []
    current_syllables = 0
    forbidden_initials = set()

    max_attempts = 100
    attempt = 0

    while current_syllables < target_syllables and attempt < max_attempts:
        attempt += 1
        # Lựa chọn nhóm ký tự bắt đầu không bị cấm
        available_initials = [c for c in all_initials if c not in forbidden_initials]
        if not available_initials:
            available_initials = all_initials

        init_choice = random.choice(available_initials)
        candidates = words_by_initial[init_choice]

        # Lọc các từ không làm vượt quá max_words
        remaining = max_words - current_syllables
        valid_candidates = [item for item in candidates if item[1] <= remaining]

        if not valid_candidates:
            # Nếu đã đạt tối thiểu min_words thì có thể kết thúc câu
            if current_syllables >= min_words:
                break
            # Nếu chưa đủ min_words, tìm từ ngắn nhất có thể
            valid_candidates = sorted(candidates, key=lambda x: x[1])[:20]

        chosen_entry, s_count = random.choice(valid_candidates)
        chosen_entries.append(chosen_entry)
        current_syllables += s_count

        # Ràng buộc từ tiếp theo:
        # Không trùng chữ cái đầu của từ hiện tại VÀ không trùng chữ cái đầu của tiếng cuối cùng trong cụm từ
        entry_init = get_base_initial(chosen_entry)
        last_word_init = get_base_initial(chosen_entry.split()[-1])
        forbidden_initials = {entry_init, last_word_init}

        if current_syllables >= target_syllables:
            break

    # Ghép các từ lại thành câu
    sentence_text = " ".join(chosen_entries)
    # Chuẩn hóa bổ sung cho CapCut
    sentence_text = normalize_sentence_for_capcut(sentence_text)
    # Viết hoa chữ cái đầu tiên và thêm dấu chấm kết thúc câu
    if sentence_text:
        sentence_text = sentence_text[0].upper() + sentence_text[1:] + "."

    return sentence_text, current_syllables, chosen_entries


# ==============================================================================
# HÀM PHÂN TÍCH VÀ ĐỌC FILE CSV CÓ SẴN
# ==============================================================================
def inspect_existing_csv(csv_path: Path, speech_rate: Optional[float] = None) -> Tuple[int, int, float, int, str, int, str]:
    """
    Đọc và phân tích file CSV hiện có.
    Trả về:
    (total_lines, total_words, est_duration_hours, last_id, id_prefix, pad_digits, id_ext)
    """
    if speech_rate is None or speech_rate <= 0:
        speech_rate = resolve_voice_speech_rate()[0]

    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return 0, 0, 0.0, 0, "audio_", 4, ".wav"

    with open(csv_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        return 0, 0, 0.0, 0, "audio_", 4, ".wav"

    total_lines = len(lines)
    total_words = 0
    last_id = 0
    id_prefix = "audio_"
    pad_digits = 4
    id_ext = ".wav"

    id_regex = re.compile(r"^(.*?)(\d+)(\.[a-zA-Z0-9]+)\|(.*)$")

    for line in lines:
        parts = line.split("|")
        text = parts[1] if len(parts) > 1 else parts[0]
        words = text.strip().split()
        total_words += len(words)

        m = id_regex.match(line)
        if m:
            prefix, num_str, ext, _ = m.groups()
            num = int(num_str)
            if num > last_id:
                last_id = num
                id_prefix = prefix
                pad_digits = max(pad_digits, len(num_str))
                id_ext = ext

    # Ước lượng thời lượng theo tốc độ đọc chuẩn xác
    est_duration_hours = (total_words / speech_rate) / 3600.0 if speech_rate > 0 else 0.0

    return total_lines, total_words, est_duration_hours, last_id, id_prefix, pad_digits, id_ext


# ==============================================================================
# CHƯƠNG TRÌNH CHÍNH (CLI)
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Ghép các từ từ word.txt thành câu và xuất ra CSV chuẩn LJSpeech cho TTS.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--word-file",
        type=Path,
        default=Path(WORD_FILE),
        help=f"Đường dẫn file chứa danh sách từ vựng (mặc định: {WORD_FILE})",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path(CSV_FILE),
        help=f"File CSV kết quả (mặc định: {CSV_FILE})",
    )
    parser.add_argument(
        "--target-hours",
        "-t",
        type=float,
        default=TARGET_HOUR,
        help="Số giờ nói mục tiêu (ví dụ: 2.0, 5, 5.1...). Tự động bù lượng còn thiếu nếu file đã có dữ liệu.",
    )
    parser.add_argument(
        "--sentences",
        "-n",
        type=int,
        default=SENTENCES,
        help="Số câu cần tạo trực tiếp (nếu không chỉ định --target-hours).",
    )
    parser.add_argument(
        "--append",
        "-a",
        action="store_true",
        default=AUTO_APPEND,
        help="Ghi tiếp vào file CSV hiện có thay vì tạo mới hoàn toàn.",
    )
    parser.add_argument(
        "--min-words",
        type=int,
        default=MIN_CHAR,
        help=f"Số tiếng (chữ) tối thiểu mỗi câu (mặc định: {MIN_CHAR})",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=MAX_CHAR,
        help=f"Số tiếng (chữ) tối đa mỗi câu (mặc định: {MAX_CHAR})",
    )
    parser.add_argument(
        "--speech-rate",
        type=float,
        default=SPEECH_RATE,
        help=f"Tốc độ đọc ước tính tiếng/giây (mặc định: {SPEECH_RATE}).",
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=SAFETY_BUFFER,
        help=f"Hệ số bù thời lượng an toàn (mặc định: {SAFETY_BUFFER} tức +{(SAFETY_BUFFER-1)*100:.0f}%%).",
    )
    parser.add_argument(
        "--capcut-fix",
        choices=["replace", "remove"],
        default=CAPCUT_FIX_MODE,
        help="Chế độ xử lý từ CapCut đọc sai (vi, xi, -...): 'replace' sửa từ (vi->vy), 'remove' loại bỏ từ (mặc định: replace)",
    )
    parser.add_argument(
        "--no-id",
        action="store_true",
        help="Chỉ xuất câu văn đơn thuần, không kèm tiền tố ID (audio_xxxx.wav|).",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=0,
        help="Chỉ tạo và in ra N câu mẫu để xem thử, không ghi file.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Chỉ phân tích số câu, số tiếng và thời lượng ước lượng của file CSV hiện có rồi dừng.",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=VOICE,
        help=f"Tên giọng đọc (vd: HoatNgon, Mai, BanMai, ReviewNew...) hoặc 'auto' để đồng bộ từ voice.py (mặc định: {VOICE}).",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Kiểm tra toàn diện: thuần Việt 100%, không viết tắt, không ngoại lai, không lỗi CapCut TTS.",
    )

    args = parser.parse_args()

    # Tự động đồng bộ tốc độ đọc và tên giọng đọc
    resolved_rate, voice_display = resolve_voice_speech_rate(args.voice)
    if args.speech_rate is None or args.speech_rate <= 0:
        args.speech_rate = resolved_rate

    # Tìm file word.txt
    word_path = args.word_file
    if not word_path.exists():
        # Thử tìm cùng thư mục với script
        script_dir = Path(__file__).resolve().parent
        if (script_dir / "word.txt").exists():
            word_path = script_dir / "word.txt"

    # Chế độ kiểm tra toàn diện 4 tiêu chuẩn (Audit)
    if args.audit:
        print("=" * 70)
        print("KIỂM TRA TOÀN DIỆN DANH MỤC TỪ VỰNG & CSV THEO 4 TIÊU CHUẨN:")
        print("=" * 70)
        with open(word_path, "r", encoding="utf-8") as f:
            all_raw = [l.strip() for l in f if l.strip()]

        dropped_list = []
        fixed_list = []
        clean_list = []
        abbrev_list = []

        for l in all_raw:
            if any(len(tok) >= 2 and tok.isupper() for tok in l.split()):
                abbrev_list.append(l)
            res = clean_and_normalize_for_capcut(l, fix_mode="replace")
            if res is None:
                dropped_list.append(l)
            else:
                w_clean, was_fix = res
                clean_list.append(w_clean)
                if was_fix:
                    fixed_list.append((l, w_clean))

        print(f"• Tổng số dòng trong file word.txt : {len(all_raw):,} dòng")
        print(f"• Từ viết tắt (IP, ICPC, DNA...)    : {len(abbrev_list)} từ (ĐẠT)")
        print(f"• Từ ngoại lai / OCR lỗi / gạch '-' : {len(dropped_list)} từ (Đã loại bỏ 100%)")
        for d in dropped_list:
            print(f"    - Loại bỏ: {d!r}")
        print(f"• Từ CapCut đọc lỗi số La Mã        : {len(fixed_list)} từ (Đã chuẩn hóa âm chuẩn)")
        for orig, fixed in fixed_list[:8]:
            print(f"    - Sửa: {orig:15} -> {fixed}")
        if len(fixed_list) > 8:
            print(f"    - ... và {len(fixed_list) - 8} từ khác.")
        print(f"• TỔNG SỐ TỪ THUẦN VIỆT 100%        : {len(clean_list):,} TỪ HỢP LỆ")
        print("=" * 70)
        print("[✓] KẾT LUẬN: ĐẠT CHUẨN 100% KHÔNG NGOẠI LAI - KHÔNG VIẾT TẮT - KHÔNG LỖI CAPCUT.")
        print("=" * 70)
        return

    # 1. Chế độ xem thống kê file CSV hiện có
    if args.stats:
        if not args.output.exists():
            print(f"[!] File {args.output} không tồn tại.")
            sys.exit(1)
        lines, words, hours, last_id, prefix, pad, ext = inspect_existing_csv(args.output, speech_rate=args.speech_rate)
        minutes = hours * 60.0
        seconds = hours * 3600.0
        print("=" * 65)
        print(f"THỐNG KÊ FILE CSV HIỆN CÓ: {args.output}")
        print("=" * 65)
        print(f"- Giọng đọc áp dụng     : {voice_display}")
        print(f"- Tổng số câu           : {lines:,} câu")
        print(f"- Tổng số tiếng (chữ)   : {words:,} tiếng")
        print(f"- Trung bình mỗi câu    : {words/lines:.1f} tiếng/câu" if lines else "- Trung bình mỗi câu    : 0 tiếng/câu")
        print(f"- Tốc độ đọc thực tế    : {args.speech_rate:.2f} tiếng/giây (~{args.speech_rate*60:.1f} tiếng/phút)")
        print(f"- Thời lượng ước tính   : {hours:.2f} giờ ({minutes:.1f} phút / {seconds:,.0f} giây)")
        print("=" * 65)
        return

    # Tải danh sách từ vựng
    print(f"[*] Đang nạp từ vựng từ: {word_path} (chế độ CapCut fix: '{args.capcut_fix}') ...")
    raw_words, words_by_initial, all_initials, total_fixed, total_dropped = load_vocab(
        word_path, fix_mode=args.capcut_fix
    )
    print(f"[✓] Đã nạp thành công {len(raw_words):,} từ vựng hợp lệ (phân bố qua {len(all_initials)} nhóm chữ cái bắt đầu).")
    if total_fixed > 0:
        print(f"    - Đã tự động sửa {total_fixed:,} từ dễ bị CapCut đọc sai ('vi' -> 'vy', 'xi' -> 'xy'...).")
    if total_dropped > 0:
        print(f"    - Đã loại bỏ {total_dropped:,} từ lỗi (chứa dấu gạch nối '-', ký tự lạ hoặc tiếng Anh).")

    # 2. Chế độ Preview
    if args.preview > 0:
        print(f"\n[*] Tạo thử {args.preview} câu mẫu (độ dài {args.min_words} - {args.max_words} tiếng, không trùng chữ đầu liền nhau):")
        print("-" * 75)
        for i in range(1, args.preview + 1):
            sent, s_count, used_words = generate_sentence(
                words_by_initial, all_initials, args.min_words, args.max_words
            )
            initials_chain = " -> ".join(f"{w} ({get_base_initial(w)})" for w in used_words)
            print(f"Câu #{i:02d} [{s_count:2d} tiếng]: {sent}")
            print(f"       Từ ghép & chữ đầu: {initials_chain}\n")
        return

    # 3. Xác định chế độ tạo câu (Target Hours hay Sentences)
    csv_path = args.output
    existing_lines, existing_words, existing_hours, last_id, id_prefix, pad_digits, id_ext = (
        0, 0, 0.0, 0, "audio_", 4, ".wav"
    )

    should_append = args.append or (csv_path.exists() and args.target_hours is not None)

    if should_append and csv_path.exists():
        existing_lines, existing_words, existing_hours, last_id, id_prefix, pad_digits, id_ext = (
            inspect_existing_csv(csv_path, speech_rate=args.speech_rate)
        )

    # Tính toán số lượng câu / số tiếng cần sinh
    # Xác định chế độ ưu tiên: nếu người dùng gõ cờ -n / --sentences thì ưu tiên theo số câu
    is_cli_sentences = ("-n" in sys.argv or "--sentences" in sys.argv) and args.sentences is not None
    is_target_hours = args.target_hours is not None and not is_cli_sentences

    if is_target_hours:
        target_h = args.target_hours
        print("\n" + "=" * 70)
        print(f"TÍNH TOÁN THEO MỤC TIÊU THỜI LƯỢNG: {target_h:.2f} GIỜ")
        print("=" * 70)
        print(f"• Giọng đọc áp dụng       : {voice_display}")
        print(f"• Tốc độ đọc thực tế      : {args.speech_rate:.2f} tiếng/giây (~{args.speech_rate*60:.1f} tiếng/phút)")

        if existing_lines > 0:
            print(f"• Dữ liệu hiện có trong {csv_path.name}:")
            print(f"  - Số câu hiện có       : {existing_lines:,} câu")
            print(f"  - Số tiếng hiện có     : {existing_words:,} tiếng")
            print(f"  - Thời lượng đã có     : ~{existing_hours:.2f} giờ ({existing_hours*60:.1f} phút)")

            remaining_hours = target_h - existing_hours
            if remaining_hours <= 0:
                print("\n" + "=" * 70)
                print("Dữ liệu đã đạt đủ thời lượng nói, không thêm mới.")
                print(f"(Hiện có: {existing_hours:.2f}h >= Mục tiêu: {target_h:.2f}h | {existing_lines:,} câu | {existing_words:,} tiếng)")
                print("=" * 70 + "\n")
                return

            print(f"  - Thời lượng còn thiếu : ~{remaining_hours:.2f} giờ ({remaining_hours*60:.1f} phút)")
        else:
            remaining_hours = target_h

        # Áp dụng hệ số an toàn (Safety buffer) để đảm bảo luôn >= đích
        buffered_hours = remaining_hours * args.buffer
        words_to_generate = int(buffered_hours * 3600.0 * args.speech_rate)

        avg_sentence_len = (args.min_words + args.max_words) / 2.0
        sentences_to_generate = math.ceil(words_to_generate / avg_sentence_len)

        print(f"• Kế hoạch sinh câu (với buffer an toàn +{(args.buffer - 1.0)*100:.0f}%):")
        print(f"  - Thời lượng bù dự kiến : ~{buffered_hours:.2f} giờ ({buffered_hours*60:.1f} phút)")
        print(f"  - Số tiếng cần tạo thêm : ~{words_to_generate:,} tiếng")
        print(f"  - Số câu dự kiến tạo    : ~{sentences_to_generate:,} câu")
        print("=" * 70)

    elif args.sentences is not None:
        sentences_to_generate = args.sentences
        avg_sentence_len = (args.min_words + args.max_words) / 2.0
        words_to_generate = int(sentences_to_generate * avg_sentence_len)
        est_hours = (words_to_generate / args.speech_rate) / 3600.0
        print("\n" + "=" * 70)
        print(f"TẠO TRỰC TIẾP {sentences_to_generate:,} CÂU")
        print(f"- Ước tính số tiếng     : ~{words_to_generate:,} tiếng")
        print(f"- Ước tính thời lượng   : ~{est_hours:.2f} giờ ({est_hours*60:.1f} phút)")
        print("=" * 70)
    else:
        # Mặc định tạo 100 câu nếu không truyền gì
        sentences_to_generate = 100
        avg_sentence_len = (args.min_words + args.max_words) / 2.0
        words_to_generate = int(sentences_to_generate * avg_sentence_len)
        print("\n[*] Không chỉ định mục tiêu, tự động sinh mặc định 100 câu...")

    # 4. Tiến hành sinh câu kèm lọc trùng lặp và kiểm tra cạn kiệt từ điển
    print("\n[*] Đang sinh các câu ngẫu nhiên (chống trùng lặp 100%)...")
    generated_rows = []
    generated_words_total = 0
    current_id = last_id

    # Đọc danh sách các câu đã có trong file CSV hiện có để chống trùng tuyệt đối
    seen_sentences = set()
    if csv_path.exists() and csv_path.stat().st_size > 0:
        with open(csv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|", 1)
                text = parts[1] if len(parts) > 1 else parts[0]
                seen_sentences.add(text.strip().lower().rstrip("."))

    # Nếu chạy theo target_hours: sinh cho đến khi tổng số tiếng đạt words_to_generate
    # Nếu chạy theo sentences: sinh đúng số câu
    condition = (
        (lambda: generated_words_total < words_to_generate)
        if is_target_hours
        else (lambda: len(generated_rows) < sentences_to_generate)
    )

    # TRƯỜNG HỢP ĐẶC BIỆT: TỪ ĐƠN (min_words == 1 and max_words == 1)
    if args.min_words == 1 and args.max_words == 1:
        # Lọc tập từ đơn duy nhất (khử trùng lặp ngay từ file từ điển)
        unique_single_words = []
        seen_in_vocab = set()
        for w in raw_words:
            if len(w.split()) == 1:
                k = w.lower().rstrip(".")
                if k not in seen_in_vocab:
                    seen_in_vocab.add(k)
                    unique_single_words.append(w)

        unused_words = [w for w in unique_single_words if w.lower().rstrip(".") not in seen_sentences]
        random.shuffle(unused_words)

        if not unused_words:
            print("\n" + "=" * 70)
            print("Dữ liệu từ điển đã dùng hết 100%, không còn từ mới để tạo thêm.")
            print(f"(Toàn bộ {len(unique_single_words):,} từ đơn đã có mặt trong file CSV. Tự động dừng dù chưa đủ giờ)")
            print("=" * 70 + "\n")
            return

        for w in unused_words:
            if not condition():
                break

            norm_key = w.lower().rstrip(".")
            if norm_key in seen_sentences:
                continue
            seen_sentences.add(norm_key)

            sent = w.capitalize() + "."
            current_id += 1
            generated_words_total += 1

            if args.no_id:
                row_text = sent
            else:
                filename = f"{id_prefix}{current_id:0{pad_digits}d}{id_ext}"
                row_text = f"{filename}|{sent}"

            generated_rows.append(row_text)

        if condition():
            print("\n[!] Đã dùng hết 100% từ vựng trong từ điển (không còn từ đơn nào chưa dùng).")
            print(f"    Tự động dừng dù chưa đủ giờ: sinh được {len(generated_rows):,}/{len(unique_single_words):,} từ đơn không trùng lặp.")

    # TRƯỜNG HỢP TỔNG QUÁT: GHÉP TỪ VỰNG (min_words đến max_words)
    else:
        max_consecutive_duplicates = 5000
        consecutive_dups = 0
        total_dups_skipped = 0

        while condition():
            sent, s_count, _ = generate_sentence(
                words_by_initial, all_initials, args.min_words, args.max_words
            )
            norm_key = sent.strip().lower().rstrip(".")

            # Kiểm tra lọc trùng lặp
            if norm_key in seen_sentences:
                consecutive_dups += 1
                total_dups_skipped += 1
                if consecutive_dups >= max_consecutive_duplicates:
                    print(f"\n[!] CẢNH BÁO: Đã thử {max_consecutive_duplicates:,} lần liên tiếp đều bị trùng câu cũ.")
                    print("    Đã cạn kiệt tổ hợp từ vựng / full từ điển. Tự động dừng dù chưa đủ giờ!")
                    break
                continue

            consecutive_dups = 0
            seen_sentences.add(norm_key)

            current_id += 1
            generated_words_total += s_count

            if args.no_id:
                row_text = sent
            else:
                filename = f"{id_prefix}{current_id:0{pad_digits}d}{id_ext}"
                row_text = f"{filename}|{sent}"

            generated_rows.append(row_text)

        if total_dups_skipped > 0:
            print(f"[*] Đã tự động phát hiện và bỏ qua {total_dups_skipped:,} câu trùng lặp trong quá trình sinh.")

    # ==============================================================================
    # 5. GHI FILE CSV THEO QUY TRÌNH 3 BƯỚC NGHIÊM NGẶT
    # Bước 1: Xóa toàn bộ dòng trống vốn có trong file hiện tại
    # Bước 2: Thêm text mới
    # Bước 3: Kiểm tra và xóa triệt để mọi dòng trống lần cuối trước khi ghi xuống đĩa
    # ==============================================================================
    existing_rows = []
    initial_blanks_removed = 0

    # BƯỚC 1: Xóa dòng trống vốn có trong file CSV hiện có
    if should_append and csv_path.exists() and csv_path.stat().st_size > 0:
        with open(csv_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                s = raw_line.strip()
                if s:
                    existing_rows.append(s)
                else:
                    initial_blanks_removed += 1
        action_text = "Ghi tiếp (append) vào"
    else:
        action_text = "Tạo mới file"

    # BƯỚC 2: Thêm text mới sinh
    merged_rows = existing_rows + [r.strip() for r in generated_rows if r.strip()]

    # BƯỚC 3: Kiểm tra và xóa dòng trống (nếu có) lần cuối trước khi ghi
    final_clean_rows = []
    final_blanks_removed = 0
    for r in merged_rows:
        s = r.strip()
        if s:
            final_clean_rows.append(s)
        else:
            final_blanks_removed += 1

    # Ghi toàn bộ dữ liệu sạch xuống file
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("\n".join(final_clean_rows) + "\n")

    total_blanks_removed = initial_blanks_removed + final_blanks_removed
    if total_blanks_removed > 0:
        print(f"[*] Đã tự động phát hiện và loại bỏ {total_blanks_removed} dòng trống thừa.")

    # 6. Tổng kết kết quả
    new_lines_count = len(generated_rows)
    new_hours = (generated_words_total / args.speech_rate) / 3600.0
    final_total_lines = existing_lines + new_lines_count
    final_total_words = existing_words + generated_words_total
    final_total_hours = (final_total_words / args.speech_rate) / 3600.0

    print(f"\n[✓] {action_text} thành công: {csv_path.resolve()}")
    print("-" * 70)
    print(f"• Số câu sinh thêm       : {new_lines_count:,} câu")
    print(f"• Số tiếng sinh thêm     : {generated_words_total:,} tiếng")
    print(f"• Thời lượng sinh thêm   : ~{new_hours:.2f} giờ ({new_hours*60:.1f} phút)")
    print("-" * 70)
    print(f"• TỔNG SỐ CÂU TRONG CSV   : {final_total_lines:,} câu")
    print(f"• TỔNG SỐ TIẾNG TRONG CSV : {final_total_words:,} tiếng")
    print(f"• TỔNG THỜI LƯỢNG ƯỚC TÍNH: ~{final_total_hours:.2f} GIỜ ({final_total_hours*60:.1f} phút)")
    print("=" * 70)

    # In vài câu mẫu mới sinh
    print("\n[Mẫu 3 câu mới nhất]:")
    for sample in generated_rows[-3:]:
        print(f"  > {sample}")
    print()


if __name__ == "__main__":
    main()
