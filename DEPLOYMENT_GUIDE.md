# Azure Functions 배포 가이드

## Kudu 재시작 오류 해결

### 오류 증상
```
[KuduSpecializer] Kudu has been restarted during deployment
```

### 해결 방법

#### 1. 로컬에서 배포 준비

```bash
# 가상환경 정리
rm -rf venv .venv __pycache__

# 새 가상환경 생성
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

#### 2. Azure Portal에서 설정 확인

**Application Settings**에 다음 환경 변수가 있는지 확인:

```
OPENAI_API_KEY=your_key
MONGODB_URI=your_mongodb_uri
MONGODB_DATABASE=your_database
OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
OPENAI_API_VERSION=2024-02-15-preview
OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-ada-002
OPENAI_CHAT_DEPLOYMENT=gpt-4
```

#### 3. 배포 옵션

##### Option A: VS Code에서 배포

1. Azure Functions 확장 설치
2. 함수 앱 우클릭 → "Deploy to Function App"
3. `.funcignore` 설정 확인

##### Option B: Azure CLI로 배포

```bash
# 로그인
az login

# 함수 앱에 배포
az functionapp deployment source config-zip \
  --resource-group <your-resource-group> \
  --name <your-function-app-name> \
  --src <path-to-zip-file>
```

##### Option C: GitHub Actions 배포 (권장)

1. Azure Portal에서 Deployment Center → GitHub 선택
2. 저장소 연결
3. `.github/workflows/azure-functions.yml` 자동 생성

#### 4. 배포 후 확인

```bash
# 로그 스트리밍
az functionapp log tail --name <your-function-app-name> --resource-group <your-resource-group>

# 함수 테스트
curl https://<your-function-app-name>.azurewebsites.net/api/question_stream \
  -H "Content-Type: application/json" \
  -d '{"Conversation": [{"speaker": "human", "utterance": "테스트"}]}'
```

## 일반적인 배포 문제

### 1. 모듈을 찾을 수 없음

**증상**: `ModuleNotFoundError: No module named 'xxx'`

**해결**:
```bash
# requirements.txt에 누락된 패키지 추가
pip freeze > requirements.txt
```

### 2. 타임아웃 오류

**증상**: `Function execution timeout`

**해결**: `host.json`에서 타임아웃 증가
```json
{
  "functionTimeout": "00:10:00"
}
```

### 3. MongoDB 연결 실패

**증상**: `ServerSelectionTimeoutError`

**해결**:
1. Azure Portal에서 MongoDB IP 화이트리스트 확인
2. Azure Functions 아웃바운드 IP 추가
3. 연결 문자열에 `retryWrites=true&w=majority` 포함 확인

### 4. OpenAI API 오류

**증상**: `401 Unauthorized` 또는 `404 Not Found`

**해결**:
1. `OPENAI_API_KEY` 환경 변수 확인
2. `OPENAI_ENDPOINT` URL 확인 (슬래시 포함)
3. 배포 이름이 실제 배포와 일치하는지 확인

## 배포 체크리스트

- [ ] `requirements.txt`에 모든 의존성 포함
- [ ] `.funcignore`로 불필요한 파일 제외
- [ ] `local.settings.json`의 환경 변수를 Azure Portal에 추가
- [ ] `host.json`에서 적절한 타임아웃 설정
- [ ] MongoDB 네트워크 접근 허용
- [ ] OpenAI 리소스 접근 권한 확인
- [ ] Python 버전 일치 (3.9, 3.10, 또는 3.11)

## 모니터링

### Application Insights 확인

```bash
# Azure Portal에서:
# Function App → Application Insights → Logs

# 쿼리 예시:
traces
| where timestamp > ago(1h)
| where severityLevel >= 2
| order by timestamp desc
```

### 실시간 로그

```bash
az webapp log tail --name <your-function-app-name> --resource-group <your-resource-group>
```

## 롤백

문제 발생 시 이전 배포로 롤백:

1. Azure Portal → Deployment Center
2. "Logs" 탭에서 성공한 이전 배포 선택
3. "Redeploy" 클릭
