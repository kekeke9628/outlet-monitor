/**
 * outlet-monitor 대시보드의 "지금 업데이트" 버튼이 호출하는 프록시.
 * GitHub PAT(GITHUB_TOKEN)은 Cloudflare Worker 시크릿으로만 존재하고 클라이언트에는 절대 노출되지 않는다.
 * 이 Worker가 하는 일은 딱 하나: 정적 대시보드 대신 GitHub Actions workflow_dispatch 를 호출하는 것.
 */

const OWNER = "kekeke9628";
const REPO = "outlet-monitor";
const WORKFLOW_FILE = "collect.yml";
const ALLOWED_ORIGIN = "https://kekeke9628.github.io";

// 아주 가벼운 남용 방지: 앱 시크릿(비밀 GitHub 자격증명이 아님, 그냥 임의 요청 차단용) +
// 같은 인스턴스가 살아있는 동안의 최소 트리거 간격. Worker 는 요청마다 새 인스턴스일 수 있어
// 완벽한 rate-limit은 아니지만(KV 없이 무료로 유지하기 위한 절충), GitHub 쪽 workflow_dispatch
// 자체도 남용에 안전하므로(퍼블릭 레포 무료 Actions, 쓰기 권한도 이 레포 하나로 한정된 토큰) 이 정도로 충분하다.
let lastTriggeredAt = 0;
const MIN_INTERVAL_MS = 2 * 60 * 1000; // 2분

function cors(resp) {
  resp.headers.set("Access-Control-Allow-Origin", ALLOWED_ORIGIN);
  resp.headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  resp.headers.set("Access-Control-Allow-Headers", "Content-Type, X-App-Secret");
  return resp;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return cors(new Response(null, { status: 204 }));
    }
    if (request.method !== "POST") {
      return cors(new Response("Method Not Allowed", { status: 405 }));
    }

    const appSecret = request.headers.get("X-App-Secret") || "";
    if (!env.APP_SECRET || appSecret !== env.APP_SECRET) {
      return cors(new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }));
    }

    const now = Date.now();
    if (now - lastTriggeredAt < MIN_INTERVAL_MS) {
      const waitSec = Math.ceil((MIN_INTERVAL_MS - (now - lastTriggeredAt)) / 1000);
      return cors(new Response(JSON.stringify({ ok: false, error: `too_soon`, retryAfterSec: waitSec }), {
        status: 429,
        headers: { "Content-Type": "application/json" },
      }));
    }

    const ghResp = await fetch(
      `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "outlet-monitor-trigger-worker",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );

    if (ghResp.status === 204) {
      lastTriggeredAt = now;
      return cors(new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }

    const detail = await ghResp.text();
    return cors(new Response(JSON.stringify({ ok: false, error: "github_api_error", status: ghResp.status, detail }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    }));
  },
};
