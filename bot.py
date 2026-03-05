import shutil
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

import yt_dlp
import subprocess

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN 없음")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ── 쿠키 설정 ────────────────────────────────────────────────
COOKIE_PATH = None

if os.getenv("RENDER"):
    SECRET_PATH = "/etc/secrets/cookies.txt"
    RUNTIME_PATH = "/tmp/cookies.txt"
    if os.path.exists(SECRET_PATH):
        shutil.copy(SECRET_PATH, RUNTIME_PATH)
        COOKIE_PATH = RUNTIME_PATH
        print("Render 쿠키 → /tmp 복사 완료")
    else:
        print("Render Secret cookies.txt 없음")
else:
    if os.path.exists("cookies.txt"):
        COOKIE_PATH = "cookies.txt"
        print("로컬 쿠키 사용")
    else:
        print("로컬 cookies.txt 없음")

if COOKIE_PATH and not os.path.exists(COOKIE_PATH):
    print(f"[쿠키 경고] {COOKIE_PATH} 없음 → 로그인 없이 시도")
    COOKIE_PATH = None

# ── URL 분류 ─────────────────────────────────────────────────
def classify_url(url: str) -> str | None:
    if "instagram.com" in url:
        return "instagram"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "vk.com" in url or "vk.ru" in url or "vkvideo.ru" in url:
        return "vk"
    return None

# ── 임시 URL 저장소 (chat_id → url) ──────────────────────────
pending: dict[int, str] = {}

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
    url = message.text.strip()
    platform = classify_url(url)

    if platform is None:
        await message.answer("인스타그램, 유튜브, VK 링크만 가능합니다.")
        return

    if platform in ("instagram", "vk"):
        await message.answer("⏬ 다운로드 중...")
        await download_and_send(message, url, mode="video")

    elif platform == "youtube":
        pending[message.chat.id] = url

        kb = InlineKeyboardBuilder()
        kb.button(text="🎬 MP4 (720p)", callback_data="yt_video")
        kb.button(text="🎵 MP3",        callback_data="yt_mp3")
        kb.adjust(2)

        await message.answer("형식을 선택하세요:", reply_markup=kb.as_markup())

# ── 유튜브 옵션 콜백 ─────────────────────────────────────────
@dp.callback_query(F.data.startswith("yt_"))
async def yt_callback(call: CallbackQuery):
    url = pending.pop(call.message.chat.id, None)
    if not url:
        await call.answer("링크가 만료됐습니다. 다시 보내주세요.", show_alert=True)
        return

    mode = "mp3" if call.data == "yt_mp3" else "video"
    await call.message.edit_text("⏬ 다운로드 중...")
    await download_and_send(call.message, url, mode=mode)

# ── 공통 다운로드 & 전송 함수 ────────────────────────────────
async def download_and_send(message: Message, url: str, mode: str = "video"):
    # 폴더 정리
    for f in DOWNLOAD_DIR.iterdir():
        try:
            f.unlink()
        except Exception:
            pass

    try:
        if mode == "mp3":
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': str(DOWNLOAD_DIR / '%(id)s.%(ext)s'),
                'noplaylist': True,
                'quiet': True,
                'cookiefile': COOKIE_PATH,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:
            ydl_opts = {
                'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
                'outtmpl': str(DOWNLOAD_DIR / '%(id)s.%(ext)s'),
                'noplaylist': True,
                'quiet': True,
                'merge_output_format': 'mp4',
                'cookiefile': COOKIE_PATH,
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        video_id = info.get('id')
        title    = info.get('title', 'video')[:50]

        # ── MP3 전송 ──────────────────────────────────────────
        if mode == "mp3":
            mp3_file = next(DOWNLOAD_DIR.glob(f"{video_id}*.mp3"), None)
            if not mp3_file:
                await message.answer("❌ MP3 변환 실패")
                return

            await message.answer_audio(
                FSInputFile(mp3_file),
                title=title,
                caption="🎵 다운로드 완료"
            )
            mp3_file.unlink(missing_ok=True)
            return

        # ── 영상 전송 ─────────────────────────────────────────
        raw_file = DOWNLOAD_DIR / f"{video_id}.mp4"
        if not raw_file.exists():
            found = next(DOWNLOAD_DIR.glob(f"{video_id}.*"), None)
            if not found:
                await message.answer("❌ 파일 생성 실패")
                return
            raw_file = found

        # faststart 적용
        final_file = DOWNLOAD_DIR / f"fs_{video_id}.mp4"
        subprocess.run(
            ['ffmpeg', '-y', '-i', str(raw_file),
             '-c', 'copy', '-map', '0', '-movflags', '+faststart',
             str(final_file)],
            check=True, capture_output=True
        )

        await message.answer_video(
            FSInputFile(final_file),
            caption=f"🎬 {title}\n다운로드 완료",
            supports_streaming=True,
        )

        raw_file.unlink(missing_ok=True)
        final_file.unlink(missing_ok=True)

    except Exception as e:
        await message.answer(f"❌ 오류: {str(e)}")

# ── 실행 ─────────────────────────────────────────────────────
async def main():
    print("봇 시작")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())