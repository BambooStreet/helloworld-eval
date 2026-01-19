# 1. 파이썬 3.10 버전(가벼운 버전)을 기반으로 합니다.
FROM python:3.10-slim

# 2. 작업 폴더를 /app으로 설정합니다.
WORKDIR /app

# 3. 필요한 패키지 목록을 먼저 복사하고 설치합니다 (캐시 효율화).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 나머지 모든 소스 코드를 복사합니다.
COPY . .

# 5. Uvicorn으로 main.py의 app을 80번 포트에서 실행합니다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]