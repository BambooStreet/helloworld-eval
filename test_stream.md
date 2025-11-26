# 스트리밍 API 테스트 가이드

## Postman에서 스트리밍 테스트

### 1. 요청 설정

**Method**: POST  
**URL**: `{{base_url}}/question_stream`

**Headers**:
```
Content-Type: application/json
```

**Body** (raw JSON):
```json
{
  "Conversation": [
    {"speaker": "human", "utterance": "외국인 근로자 비자에 대해 알려주세요."}
  ]
}
```

### 2. Postman에서 스트리밍 확인

Postman은 SSE를 완벽하게 지원하지는 않지만, 다음과 같이 확인 가능합니다:

1. **Send 버튼 클릭**
2. **Response 탭에서 "Stream" 옵션 확인**
3. 데이터가 실시간으로 추가되는 것을 확인

**예상 응답 형식**:
```
data: {"type": "metadata", "retrieved_doc_ids": ["doc1", "doc2"]}

data: {"type": "content", "content": "외국인"}

data: {"type": "content", "content": " 근로자"}

data: {"type": "content", "content": " 비자는"}

...

data: {"type": "done"}
```

### 3. cURL로 테스트 (권장)

터미널에서 실시간 스트리밍 확인:

```bash
curl -N -X POST http://localhost:7071/api/question_stream \
  -H "Content-Type: application/json" \
  -d '{
    "Conversation": [
      {"speaker": "human", "utterance": "외국인 근로자 비자에 대해 알려주세요."}
    ]
  }'
```

### 4. Python 클라이언트 예시

```python
import requests
import json

url = "http://localhost:7071/api/question_stream"
headers = {"Content-Type": "application/json"}
data = {
    "Conversation": [
        {"speaker": "human", "utterance": "외국인 근로자 비자에 대해 알려주세요."}
    ]
}

with requests.post(url, json=data, headers=headers, stream=True) as response:
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode('utf-8')
            if decoded_line.startswith('data: '):
                json_data = json.loads(decoded_line[6:])
                
                if json_data['type'] == 'metadata':
                    print(f"문서 ID: {json_data['retrieved_doc_ids']}")
                elif json_data['type'] == 'content':
                    print(json_data['content'], end='', flush=True)
                elif json_data['type'] == 'done':
                    print("\n[완료]")
                elif json_data['type'] == 'error':
                    print(f"\n[에러]: {json_data['error']}")
```

### 5. JavaScript 클라이언트 예시

```javascript
const eventSource = new EventSource('http://localhost:7071/api/question_stream', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        Conversation: [
            {speaker: "human", utterance: "외국인 근로자 비자에 대해 알려주세요."}
        ]
    })
});

eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'content') {
        document.getElementById('response').innerText += data.content;
    } else if (data.type === 'done') {
        eventSource.close();
        console.log('스트리밍 완료');
    }
};

eventSource.onerror = (error) => {
    console.error('스트리밍 에러:', error);
    eventSource.close();
};
```

## 비교: 일반 vs 스트리밍

| 항목 | 일반 API (`/question`) | 스트리밍 API (`/question_stream`) |
|------|----------------------|----------------------------------|
| 응답 방식 | 전체 응답 한번에 반환 | 토큰 단위로 실시간 전송 |
| 응답 시간 | 전체 생성 후 반환 | 즉시 시작, 점진적 표시 |
| 사용자 경험 | 대기 시간 길 수 있음 | 실시간 피드백 |
| Content-Type | `application/json` | `text/event-stream` |
| 적합한 상황 | 배치 처리, 간단한 질문 | 긴 답변, 실시간 UI |

## 주의사항

1. **Azure Functions 제한**: Azure Functions의 타임아웃 설정 확인 필요
2. **Postman 제한**: SSE 완벽 지원 안 됨, cURL이나 브라우저 사용 권장
3. **버퍼링**: 프록시/로드밸런서 버퍼링 설정 확인 필요
