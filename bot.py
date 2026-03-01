import asyncio
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile

import yt_dlp

# ====================== 설정 ======================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN이 .env 또는 환경변수에 없습니다.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# 쿠키 파일 경로 자동 선택 (로컬 vs Render)
if os.getenv("RENDER"):
    COOKIE_PATH = "/etc/secrets/cookies.txt"
else:
    COOKIE_PATH = "cookies.txt"

if not os.path.exists(COOKIE_PATH):
    print(f"[WARNING] 쿠키 파일 없음: {COOKIE_PATH}")
    print("로컬 → cookies.txt 파일 확인 / Render → Secret Files 업로드 확인")

# ====================== 핸들러 ======================
@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "인스타 릴스/비디오 다운로더 봇 📹\n"
        "공개 링크 보내주세요 → MP4 전송합니다\n"
        "※ 쿠키는 로컬 cookies.txt 또는 Render Secret Files 사용"
    )


@dp.message()
async def download_handler(message: Message):
    url = message.text.strip()
    if "instagram.com" not in url:
        await message.answer("인스타그램 링크만 가능합니다.")
        return

    await message.answer("다운로드 중... (모바일 호환 재인코딩 포함) ⏳")

    try:
        ydl_opts = {
            'format': 'bv*+ba/b',
            'outtmpl': str(DOWNLOAD_DIR / '%(id)s.%(ext)s'),
            'noplaylist': True,
            'quiet': False,
            'no_warnings': True,
            'continuedl': True,
            'retries': 20,
            'fragment_retries': 20,
            'merge_output_format': 'mp4',
            'cookiefile': COOKIE_PATH,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # yt-dlp가 병합한 mp4 파일 찾기
        merged_file = None
        for f in DOWNLOAD_DIR.iterdir():
            if info.get('id') in f.name and f.suffix.lower() == '.mp4':
                merged_file = f
                break

        if not merged_file or not merged_file.exists():
            await message.answer("병합된 파일을 찾지 못했습니다.")
            return

        print(f"[DEBUG] 병합 파일 발견: {merged_file}")

        # 모바일 호환 최적화 재인코딩 (원본 비율 유지)
        output_file = DOWNLOAD_DIR / f"mobile_{info.get('id')}.mp4"

        cmd = [
            'ffmpeg', '-y',
            '-i', str(merged_file),
            '-c:v', 'libx264',
            '-profile:v', 'baseline',      # 모바일 호환 최고 프로파일
            '-level', '3.0',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',     # 핵심! 모바일 재생 고정 문제 해결
            '-crf', '23',
            '-preset', 'medium',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            str(output_file)
        ]

        print("[DEBUG] ffmpeg cmd:", " ".join(cmd))

        result = subprocess.run(cmd, capture_output=True, text=True)

        print(f"[DEBUG] ffmpeg returncode: {result.returncode}")
        if result.returncode != 0:
            print(f"[DEBUG] ffmpeg stderr: {result.stderr}")
            await message.answer(f"재인코딩 실패:\n{result.stderr[:400]}...")
            return

        file_size_mb = output_file.stat().st_size / (1024 * 1024)
        if file_size_mb > 50:
            await message.answer(f"파일 크기 초과 ({file_size_mb:.1f} MB)")
            output_file.unlink(missing_ok=True)
            return

        await message.answer_video(
            FSInputFile(output_file),
            caption=f"완료! (모바일 최적화) 🎉\n{url[:120]}...",
            supports_streaming=True
        )

        # 정리
        merged_file.unlink(missing_ok=True)
        output_file.unlink(missing_ok=True)

    except yt_dlp.utils.DownloadError as e:
        await message.answer(f"다운로드 실패:\n{str(e)}")
    except Exception as e:
        await message.answer(f"오류:\n{str(e)}")


# ====================== 실행 ======================
async def main():
    print("봇 시작됨")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())