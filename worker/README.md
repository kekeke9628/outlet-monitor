# outlet-monitor 업데이트 트리거 Worker

대시보드의 "지금 업데이트" 버튼이 호출하는 작은 프록시. GitHub PAT는 여기(Cloudflare)에만
저장되고 대시보드(정적 페이지)에는 절대 들어가지 않는다.

## 진행 순서 (직접 해야 하는 부분)

### 1. Cloudflare 무료 계정 가입
https://dash.cloudflare.com/sign-up — 이메일만 있으면 됨, 카드 등록 불필요.

### 2. 터미널에서 로그인
이 폴더(`outlet-monitor/worker`)에서:
```bash
npx wrangler login
```
브라우저가 열리면 방금 만든 Cloudflare 계정으로 로그인 + 권한 허용.

### 3. GitHub Fine-grained PAT 생성 (이 레포 전용, 최소 권한)
1. https://github.com/settings/personal-access-tokens/new 접속
2. **Repository access** → "Only select repositories" → `outlet-monitor` 선택
3. **Permissions** → **Repository permissions** → **Actions** → **Read and write** (다른 권한은 전부 No access로 둠)
4. 만료기간은 원하는 대로(예: 1년) 설정 후 Generate token
5. 생성된 토큰을 복사 (한 번만 보여줌)

### 4. Worker 배포
```bash
cd worker
npm install
npx wrangler deploy
```
배포가 끝나면 터미널에 `https://outlet-monitor-trigger.<당신의-서브도메인>.workers.dev` 같은
URL이 출력됨 — 이 URL을 기억해둘 것.

### 5. 시크릿 등록 (터미널에 직접 입력 — 절대 채팅에 붙여넣지 말 것)
```bash
npx wrangler secret put GITHUB_TOKEN
```
→ 프롬프트가 뜨면 3번에서 만든 GitHub 토큰을 붙여넣고 Enter.

```bash
npx wrangler secret put APP_SECRET
```
→ 아무 임의의 긴 문자열이나 직접 만들어서 입력 (예: `openssl rand -hex 16` 결과, 또는 그냥
아무 문장). 이건 GitHub 자격증명이 아니라 단순 남용 방지용 문자열이라 스스로 정하면 됨.

### 6. 대시보드에 값 채우기
`../index.html` 에서 다음 두 줄을 찾아서:
```js
const TRIGGER_ENDPOINT = "REPLACE_WITH_WORKER_URL";
const TRIGGER_APP_SECRET = "REPLACE_WITH_APP_SECRET";
```
- `TRIGGER_ENDPOINT` → 4번에서 나온 workers.dev URL
- `TRIGGER_APP_SECRET` → 5번에서 APP_SECRET에 넣은 바로 그 문자열 (이건 클라이언트 코드에
  그대로 들어가도 되는 값 — GitHub 토큰과 달리 노출돼도 안전하도록 설계됨)

저장 후 커밋/푸시하면 대시보드의 "지금 업데이트" 버튼이 활성화된다.

## 참고
- `src/index.js`: 2분 최소 간격 남용 방지 + GitHub `workflow_dispatch` 호출만 함. 그 외 로직 없음.
- 비용: Cloudflare Workers 무료 티어(하루 100,000 요청)로 개인 사용에 충분. 카드 등록 불필요.
- 기존 매일 자동 실행(cron)은 그대로 유지됨 — 이 버튼은 "지금 바로 최신화"용 추가 수단.
