import shutil
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
        # 폴더 정리
        for f in DOWNLOAD_DIR.iterdir():
            f.unlink(missing_ok=True)

        ydl_opts = {
            # 'format': 'bestvideo[height<=720]+bestaudio/best',
            'format': 'best[ext=mp4]',
            'outtmpl': str(DOWNLOAD_DIR / '%(id)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'merge_output_format': 'mp4',
            'cookiefile': COOKIE_PATH if COOKIE_PATH else None,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        merged_file = DOWNLOAD_DIR / f"{info.get('id')}.mp4"
        if not merged_file.exists():
            await message.answer("파일 생성 실패")
            return

        # faststart 적용 (모바일 재생 최적화)
        final_file = DOWNLOAD_DIR / f"fs_{info.get('id')}.mp4"


        cmd = [
            'ffmpeg', '-y',
            '-i', str(merged_file),
            '-c', 'copy',
            '-map', '0',
            '-movflags', '+faststart',
            str(final_file)
        ]
 

        subprocess.run(cmd, check=True)


        # 1. 미리보기: video로 전송 (빠른 확인용)
        await message.answer_video(
            FSInputFile(final_file),
            caption="Download completed 🎬\n (Telegram Player)",
            supports_streaming=True
        )


        # 정리
        merged_file.unlink(missing_ok=True)
        final_file.unlink(missing_ok=True)
   

    except Exception as e:
        await message.answer(f"오류: {str(e)}")


async def main():
    print("봇 시작")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())