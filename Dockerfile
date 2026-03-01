# Python 3.11 기반 슬림 이미지 사용 (가볍고 빠름)
FROM python:3.11-slim

# ffmpeg 설치 (Render 기본 이미지에 없으므로 꼭 설치)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 작업 디렉토리 설정
WORKDIR /app

# 의존성 먼저 복사 & 설치 (캐시 효율성)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 코드 전체 복사
COPY . .

# 봇 실행 명령어
CMD ["python", "bot.py"]