import shutil
import asyncio
import os
import time
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

import yt_dlp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 없음")

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

BASE_DOWNLOAD_DIR = Path(__file__).parent / "downloads"
BASE_DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_MB  = 50          # 텔레그램 봇 업로드 한도
PENDING_TTL  = 300         # pending URL 만료 시간 (초)

# ── 쿠키 설정 ────────────────────────────────────────────────
COOKIE_PATH: str | None = None

# bot.py 기준 절대경로로 쿠키 탐색
_BASE_DIR = Path(__file__).parent

if os.getenv("RENDER"):
    SECRET_PATH  = "/etc/secrets/cookies.txt"
    RUNTIME_PATH = "/tmp/cookies.txt"
    if os.path.exists(SECRET_PATH):
        shutil.copy(SECRET_PATH, RUNTIME_PATH)
        COOKIE_PATH = RUNTIME_PATH
        print("Render 쿠키 → /tmp 복사 완료")
    else:
        print("Render Secret cookies.txt 없음")
else:
    _local = _BASE_DIR / "cookies.txt"
    if _local.exists():
        COOKIE_PATH = str(_local.resolve())
        print(f"로컬 쿠키 사용: {COOKIE_PATH}")
    else:
        print(f"로컬 cookies.txt 없음 (탐색 경로: {_local})")

if COOKIE_PATH and not os.path.exists(COOKIE_PATH):
    print(f"[쿠키 경고] {COOKIE_PATH} 없음 → 로그인 없이 시도")
    COOKIE_PATH = None

print(f"[쿠키 최종] COOKIE_PATH = {COOKIE_PATH}")

# ── URL 분류 ─────────────────────────────────────────────────
def classify_url(url: str) -> str | None:
    if "instagram.com" in url:
        return "instagram"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "vk.com" in url or "vk.ru" in url or "vkvideo.ru" in url:
        return "vk"
    return None

# ── pending: {chat_id: (url, timestamp)} ─────────────────────
pending: dict[int, tuple[str, float]] = {}

def set_pending(chat_id: int, url: str):
    pending[chat_id] = (url, time.monotonic())

def pop_pending(chat_id: int) -> str | None:
    entry = pending.pop(chat_id, None)
    if not entry:
        return None
    url, ts = entry
    if time.monotonic() - ts > PENDING_TTL:
        return None   # 만료
    return url

# ── 유저별 다운로드 폴더 ─────────────────────────────────────
def get_user_dir(chat_id: int) -> Path:
    d = BASE_DOWNLOAD_DIR / str(chat_id)
    d.mkdir(exist_ok=True)
    return d

def cleanup_dir(d: Path):
    for f in d.iterdir():
        try:
            f.unlink()
        except Exception:
            pass
    try:
        d.rmdir()
    except Exception:
        pass

# ── /start ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "📥 다운로드 봇\n\n"
        "• 인스타그램 릴스\n"
        "• 유튜브 영상 / Shorts\n"
        "• VK 비디오\n\n"
        "링크를 보내주세요!"
    )

# ── 링크 수신 ────────────────────────────────────────────────
@dp.message()
async def handle_link(message: Message):
    url      = message.text.strip()
    platform = classify_url(url)

    if platform is None:
        await message.answer("인스타그램, 유튜브, VK 링크만 가능합니다.")
        return

    if platform in ("instagram", "vk"):
        await message.answer("⏬ 다운로드 중...")
        await download_and_send(message, url, mode="video")

    elif platform == "youtube":
        set_pending(message.chat.id, url)

        kb = InlineKeyboardBuilder()
        kb.button(text="🎬 MP4 (720p)", callback_data="yt_video")
        kb.button(text="🎵 MP3",        callback_data="yt_mp3")
        kb.adjust(2)

        await message.answer("형식을 선택하세요:", reply_markup=kb.as_markup())

# ── 유튜브 옵션 콜백 ─────────────────────────────────────────
@dp.callback_query(F.data.startswith("yt_"))
async def yt_callback(call: CallbackQuery):
    url = pop_pending(call.message.chat.id)
    if not url:
        await call.answer(
            "링크가 만료됐습니다. 다시 보내주세요.",
            show_alert=True
        )
        return

    mode = "mp3" if call.data == "yt_mp3" else "video"
    await call.message.edit_text("⏬ 다운로드 중...")
    await download_and_send(call.message, url, mode=mode)

# ── yt-dlp 동기 작업 (thread pool에서 실행) ──────────────────
def _run_ydl(ydl_opts: dict, url: str) -> dict:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)

# ── ffmpeg 비동기 실행 ────────────────────────────────────────
async def _run_ffmpeg(input_path: Path, output_path: Path):
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-c", "copy",
        "-map", "0",
        "-movflags", "+faststart",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 변환 실패")

# ── 공통 다운로드 & 전송 ─────────────────────────────────────
async def download_and_send(
    message: Message,
    url: str,
    mode: str = "video",
):
    chat_id  = message.chat.id
    user_dir = get_user_dir(chat_id)

    try:
        # 플랫폼 감지
        platform = classify_url(url)

        if mode == "mp3":
            ydl_opts = {
                "format":     "bestaudio/best",
                "outtmpl":    str(user_dir / "%(id)s.%(ext)s"),
                "noplaylist": True,
                "quiet":      True,
                "cookiefile": COOKIE_PATH,
                "postprocessors": [{
                    "key":             "FFmpegExtractAudio",
                    "preferredcodec":  "mp3",
                    "preferredquality":"192",
                }],
            }

        elif platform == "instagram":
            ydl_opts = {
                "format": "bestvideo+bestaudio/best",
                "outtmpl":             str(user_dir / "%(id)s.%(ext)s"),
                "noplaylist":          True,
                "quiet":               True,
                "merge_output_format": "mp4",
                "cookiefile":          COOKIE_PATH,
                "http_headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Mobile/15E148 Safari/604.1"
                    ),
                },
            }

        else:
            ydl_opts = {
                "format": "bestvideo[height<=720]+bestaudio/bestvideo+bestaudio/best",
                "outtmpl":             str(user_dir / "%(id)s.%(ext)s"),
                "noplaylist":          True,
                "quiet":               True,
                "merge_output_format": "mp4",
                "cookiefile":          COOKIE_PATH,
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web"],
                    }
                },
            }

        # ── 비동기로 yt-dlp 실행 ─────────────────────────────
        info = await asyncio.to_thread(_run_ydl, ydl_opts, url)

        video_id = info.get("id")
        title    = info.get("title", "video")[:50]

        # ── MP3 전송 ─────────────────────────────────────────
        if mode == "mp3":
            mp3_file = next(user_dir.glob(f"{video_id}*.mp3"), None)
            if not mp3_file:
                await message.answer("❌ MP3 변환 실패")
                return

            size_mb = mp3_file.stat().st_size / 1024 / 1024
            if size_mb > MAX_FILE_MB:
                await message.answer(
                    f"❌ 파일이 너무 큽니다 ({size_mb:.1f}MB)\n"
                    f"텔레그램 한도: {MAX_FILE_MB}MB"
                )
                return

            await message.answer_audio(
                FSInputFile(mp3_file),
                title=title,
                caption="🎵 다운로드 완료"
            )
            return

        # ── 영상 전송 ────────────────────────────────────────
        raw_file = user_dir / f"{video_id}.mp4"
        if not raw_file.exists():
            raw_file = next(user_dir.glob(f"{video_id}.*"), None)
            if not raw_file:
                await message.answer("❌ 파일 생성 실패")
                return

        # 파일 크기 체크
        size_mb = raw_file.stat().st_size / 1024 / 1024
        if size_mb > MAX_FILE_MB:
            await message.answer(
                f"❌ 파일이 너무 큽니다 ({size_mb:.1f}MB)\n"
                f"텔레그램 한도: {MAX_FILE_MB}MB"
            )
            return

        # faststart 비동기 변환
        final_file = user_dir / f"fs_{video_id}.mp4"
        await _run_ffmpeg(raw_file, final_file)

        await message.answer_video(
            FSInputFile(final_file),
            caption=f"🎬 {title}\n다운로드 완료",
            supports_streaming=True,
        )

    except Exception as e:
        await message.answer(f"❌ 오류: {str(e)}")

    finally:
        # 유저 폴더 항상 정리
        cleanup_dir(user_dir)

# ── 실행 ─────────────────────────────────────────────────────
async def main():
    print("봇 시작")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())