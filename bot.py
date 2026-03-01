import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile

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

COOKIE_PATH = "/etc/secrets/cookies.txt" if os.getenv("RENDER") else "cookies.txt"

if not os.path.exists(COOKIE_PATH):
    print(f"[쿠키 경고] {COOKIE_PATH} 없음 → 로그인 없이 시도")

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("릴스 링크 보내주세요 📹")

@dp.message()
async def handler(message: Message):
    url = message.text.strip()
    if "instagram.com" not in url:
        await message.answer("인스타 링크만 가능")
        return

    await message.answer("다운로드 중...")

    try:
        # 폴더 정리 (메모리 + 디스크 절약)
        for f in DOWNLOAD_DIR.iterdir():
            f.unlink(missing_ok=True)

        ydl_opts = {
            'format': 'bestvideo[height<=480]+bestaudio/best',  # 480p 제한 (메모리 절약 + 호환성 ↑)
            'outtmpl': str(DOWNLOAD_DIR / '%(id)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'merge_output_format': 'mp4',
            'cookiefile': COOKIE_PATH,
            
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        merged_file = DOWNLOAD_DIR / f"{info.get('id')}.mp4"
        if not merged_file.exists():
            await message.answer("파일 생성 실패")
            return

        final_file = DOWNLOAD_DIR / f"fs_{info.get('id')}.mp4"

        cmd = [
            'ffmpeg', '-y',
            '-i', str(merged_file),
            '-c', 'copy',
            '-movflags', '+faststart',
            str(final_file)
        ]

        result = subprocess.run(cmd, check=True, capture_output=True, text=True)

        if result.returncode != 0:
            await message.answer("faststart 실패 - 원본 전송")
            final_file = merged_file

        await message.answer_video(
            FSInputFile(final_file),
            caption="완료 🎉",
            supports_streaming=True
        )

        merged_file.unlink(missing_ok=True)
        final_file.unlink(missing_ok=True)

    except Exception as e:
        await message.answer(f"오류: {str(e)}")


async def main():
    print("봇 시작")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())