#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
경쟁사 현황 대시보드 — 수집 파이프라인 레퍼런스 구현
====================================================
역할: 3사(신세계/현대/롯데) × 6개 카테고리의 공개 활동을 수집·정규화·중복제거하여
      대시보드가 읽는 단일 계약(contract) 파일 data.json 을 생성한다.

설계 원칙
- 뷰(대시보드)와 데이터(크롤러)를 완전히 분리한다. 대시보드는 오직 data.json 만 읽는다.
- 각 소스는 독립 Collector 로 구현하고, 실패해도 다른 소스에 영향을 주지 않는다(부분 실패 허용).
- 저작권 보호: 원문 텍스트/이미지를 저장하지 않고 제목·기간·링크 등 사실 정보만 담는다.

실행:  python collector.py  →  data.json 생성
API 키 불필요: 뉴스·외국인동향·홈페이지=구글 뉴스 RSS, 유튜브=채널 공개 RSS,
                  인스타=반자동 입력. 순수 표준 라이브러리(외부 패키지 불필요).
"""
import os, sys, json, hashlib, tempfile, argparse, datetime as dt
import re, html as htmllib
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlparse, quote
from urllib.request import Request, urlopen
from email.utils import parsedate_to_datetime
from typing import List, Dict

KST = dt.timezone(dt.timedelta(hours=9))
# 실제 수집 시각(서버의 현재 시각). 테스트에서는 collector.NOW 를 고정해 검증한다.
NOW = dt.datetime.now(KST)
NOW_ISO = NOW.isoformat()

def log(msg: str) -> None:
    """실행 로그(표준에러). cron 로그 파일로 리다이렉트해 운영 추적에 사용."""
    print(f"[{dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)

BRANDS = {
    "sse":     {"label": "신세계", "home": "https://www.premiumoutlets.co.kr/",
                "insta": "https://www.instagram.com/premiumoutlets.korea/"},
    "hyundai": {"label": "현대",   "home": "https://www.ehyundai.com/newPortal/outlet/main.do",
                "insta": "https://www.instagram.com/hyundaioutlets/"},
    "lotte":   {"label": "롯데",   "home": "https://www.lotteshopping.com/",
                "insta": "https://www.lotteshopping.com/"},
}
# status = 기간에 걸치면 노출 / event = 발생일이 기간 안이면 노출
# 카테고리별 표시 모드의 단일 소스(SSOT). HP_CATEGORIES/collect_news/collect_tourism 이 참조한다.
CATEGORY_MODE = {
    "promotions": "status", "events": "status", "new-brands": "event",
    "social": "event", "news": "event", "foreign-trends": "event",
}

# --------------------------------------------------------------------------
# SEED: 실서비스에서는 각 Collector 가 라이브로 채우는 데이터.
# 키/네트워크가 없는 환경에서 파이프라인을 그대로 시연하기 위한 예시 데이터다.
# --------------------------------------------------------------------------
SEED = [
    # ── 프로모션 (status) ──
    dict(brand="sse", category="promotions", title="여름 시즌오프 최대 70%", type="시즌오프", start="2026-06-27", end="2026-07-13"),
    dict(brand="sse", category="promotions", title="골드 멤버스 위크 (추가 5%)", type="멤버십데이", start="2026-07-01", end="2026-07-04"),
    dict(brand="hyundai", category="promotions", title="썸머 페스타 세일", type="시즌세일", start="2026-06-20", end="2026-07-06"),
    dict(brand="hyundai", category="promotions", title="클리어런스 마켓", type="클리어런스", start="2026-06-10", end="2026-06-25"),
    dict(brand="lotte", category="promotions", title="블랙프라이스 위크", type="특가", start="2026-07-01", end="2026-07-07"),
    # ── 이벤트 (status) ──
    dict(brand="sse", category="events", title="아이스 & 스노우 존 팝업", type="체험", start="2026-07-01", end="2026-07-31"),
    dict(brand="sse", category="events", title="퓨전 전통 공연 위켄드", type="공연", start="2026-06-28", end="2026-06-29"),
    dict(brand="hyundai", category="events", title="키즈 워터플레이 존", type="체험", start="2026-06-28", end="2026-08-10"),
    dict(brand="lotte", category="events", title="봄 아트 전시 <색의 정원>", type="전시", start="2026-06-01", end="2026-06-15"),
    # ── 신규 입점 (event) ──
    dict(brand="sse", category="new-brands", title="명품 시계 편집숍 신규 오픈", type="럭셔리", date="2026-07-02"),
    dict(brand="lotte", category="new-brands", title="리빙관 확장 · 5개 브랜드 입점", type="리빙", date="2026-06-30"),
    dict(brand="hyundai", category="new-brands", title="스포츠 플래그십 리뉴얼", type="스포츠", date="2026-06-18"),
    # ── SNS (event) : 유튜브=자동 / 인스타=반자동(수동입력) ──
    dict(brand="sse", category="social", title="인스타 릴스 · 스노우존 티저", type="인스타", date="2026-07-03", collect="semi"),
    dict(brand="sse", category="social", title="유튜브 · 여름 브랜드 하울", type="유튜브", date="2026-07-01", collect="auto"),
    dict(brand="hyundai", category="social", title="인스타 · 워터플레이 오픈 안내", type="인스타", date="2026-07-02", collect="semi"),
    dict(brand="lotte", category="social", title="인스타 · 블랙프라이스 카드뉴스", type="인스타", date="2026-07-01", collect="semi"),
    dict(brand="lotte", category="social", title="인스타 · 신규 리빙관 소개", type="인스타", date="2026-06-30", collect="semi"),
    # ── 뉴스 (event) ──
    dict(brand="sse", category="news", title="여주 아울렛, 외국인 매출 비중 20% 돌파", type="매일경제", date="2026-07-03"),
    dict(brand="hyundai", category="news", title="현대아울렛 상반기 실적 발표", type="연합뉴스", date="2026-07-01"),
    dict(brand="lotte", category="news", title="롯데 프리미엄, 하반기 신규 브랜드 유치", type="뉴스핌", date="2026-06-29"),
    dict(brand="sse", category="news", title="신세계사이먼, 관광 허브 전략 공개", type="머니투데이", date="2026-06-16"),
    # ── 외국인 동향 (event) ──
    dict(brand="sse", category="foreign-trends", title="외국인 원데이투어 직행버스 노선 확대", type="서비스", date="2026-07-02"),
    dict(brand="hyundai", category="foreign-trends", title="은련(UnionPay) 결제 프로모션 개시", type="결제혜택", date="2026-06-28"),
    dict(brand="lotte", category="foreign-trends", title="다국어 안내 앱 업데이트 (영·중·일)", type="디지털", date="2026-07-01"),
    dict(brand="sse", category="foreign-trends", title="참고 · 6월 방한 외래객 전년比 +14%", type="관광통계", date="2026-06-25"),
]

def _seed(*, category=None, type_in=None, brand=None):
    out = []
    for r in SEED:
        if category and r["category"] != category: continue
        if brand and r["brand"] != brand: continue
        if type_in and r.get("type") not in type_in: continue
        out.append(dict(r))
    return out

# ==========================================================================
# Collectors — 소스별 수집기. 실서비스 라이브 로직은 docstring 의 TODO 참고.
# 각 함수는 표준 활동 dict 리스트를 반환한다.
# ==========================================================================
def collect_homepage(brand: str) -> List[Dict]:
    """홈페이지 성격의 활동(프로모션/이벤트/신규입점): 구글 뉴스 RSS (키·셀렉터 불필요).
    각 사 사이트를 직접 크롤링하는 대신, 브랜드명 + 카테고리 키워드로 공개 뉴스에서
    관련 소식을 수집한다. 별도 설정·입력 없이 바로 동작한다.
    네트워크 불가 등으로 모두 실패하면 SEED 예시로 대체."""
    out, errors = [], 0
    for category, terms in HP_CATEGORIES.items():
        q = f"{BRAND_NAME[brand]} {terms}"
        mode = CATEGORY_MODE[category]
        rows, err = _collect_rss(category, {brand: q}, HP_LOOKBACK_DAYS, HP_PER_BRAND, mode)
        out.extend(rows); errors += err
    if not out and errors:
        return _homepage_seed(brand)
    return out

def _homepage_seed(brand: str) -> List[Dict]:
    return [r for r in _seed(brand=brand)
            if r["category"] in ("promotions", "events", "new-brands")]


# --- 홈페이지(프로모션/이벤트/신규입점) 수집 설정 (구글 뉴스 RSS) --------------
# 각 사 사이트를 직접 긁지 않고, 브랜드명 + 카테고리 키워드로 공개 뉴스에서 수집한다.
# mode(status/event)는 위 CATEGORY_MODE 를 따른다(중복 정의 방지). 키워드는 조정 가능.
BRAND_NAME = {
    "sse":     "신세계 프리미엄 아울렛",
    "hyundai": "현대 프리미엄 아울렛",
    "lotte":   "롯데 프리미엄 아울렛",
}
HP_CATEGORIES = {
    "promotions": "(세일 OR 프로모션 OR 할인 OR 특가)",
    "events":     "(이벤트 OR 팝업 OR 전시 OR 체험)",
    "new-brands": "(입점 OR 오픈 OR 신규 브랜드)",
}
HP_LOOKBACK_DAYS = 30
HP_PER_BRAND = 8


def _extract_dates(text: str) -> List[str]:
    """텍스트에서 YYYY.MM.DD / YYYY-MM-DD / YYYY/M/D 형태의 날짜를 순서대로 추출."""
    out = []
    for y, m, d in re.findall(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text or ""):
        out.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    return out

def collect_news() -> List[Dict]:
    """뉴스: 구글 뉴스 RSS (API 키 불필요)."""
    out, errors = _collect_rss("news", NEWS_QUERY, NEWS_LOOKBACK_DAYS, NEWS_PER_BRAND,
                                CATEGORY_MODE["news"])
    if not out and errors:                           # 네트워크 불가 → 데모/폴백
        return _seed(category="news")
    return out


# --- 뉴스 수집 설정/보조 (구글 뉴스 RSS) --------------------------------------
NEWS_QUERY = {
    "sse":     "신세계 프리미엄 아울렛",
    "hyundai": "현대 프리미엄 아울렛",
    "lotte":   "롯데 프리미엄 아울렛",
}
NEWS_LOOKBACK_DAYS = 30      # 이 기간보다 오래된 기사는 제외
NEWS_PER_BRAND = 20          # 브랜드당 최대 반영 건수

def _google_news_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})

def _clean_news_title(title: str, source: str = None) -> str:
    """구글 뉴스 제목은 '헤드라인 - 매체' 형태 → 매체 접미사 제거 + 엔티티 복원."""
    t = htmllib.unescape(title or "").strip()
    if source and t.endswith(" - " + source):
        t = t[: -(len(source) + 3)].strip()
    return t

def _collect_rss(category, query_map, lookback_days, per_brand, mode="event"):
    """구글 뉴스 RSS 공통 수집기. 뉴스·외국인동향·홈페이지 카테고리가 공용으로 쓴다.
    반환: (활동 리스트, 실패한 브랜드 수). mode=status 면 발행일을 시작=종료로 넣는다."""
    cutoff = NOW.date() - dt.timedelta(days=lookback_days)
    seen, out, errors = set(), [], 0
    for brand, query in query_map.items():
        try:
            xml = _fetch(_google_news_url(query))
        except Exception as e:                       # 브랜드 단위 실패 격리
            errors += 1; log(f"    수집 실패({category}/{brand}): {e}"); continue
        for it in _rss_items(xml)[:per_brand]:
            title = _clean_news_title(it["title"], it.get("source"))
            url = it.get("link", "")
            if not title or not url:
                continue
            try:
                d = parsedate_to_datetime(it["pubDate"]).astimezone(KST).date()
            except Exception:
                continue
            if d < cutoff:
                continue
            key = (brand, title)
            if key in seen:
                continue
            seen.add(key)
            row = dict(brand=brand, category=category, title=title,
                       type=it.get("source") or "뉴스", url=url, source="기사")
            if mode == "status":
                # 실제 세일/행사 기간 데이터가 없어 보도일을 start=end 로 대신 채운다.
                # date_basis="reported" 로 "이 날짜는 보도일이며 실제 기간이 아님"을 명시해
                # 프론트가 "07.29–07.29" 같은 가짜 기간 대신 "보도일: 07.29" 로 표시할 수 있게 한다.
                row["start"] = row["end"] = d.isoformat()
                row["date_basis"] = "reported"
            else:
                row["date"] = d.isoformat()
            out.append(row)
    return out, errors


def collect_youtube() -> List[Dict]:
    """유튜브 공식 채널: 채널 공개 RSS 피드 (API 키 불필요).
    https://www.youtube.com/feeds/videos.xml?channel_id=... 로 최근 업로드를 받는다.
    네트워크 불가 등으로 모두 실패하면 SEED 예시로 대체."""
    cutoff = NOW.date() - dt.timedelta(days=YT_LOOKBACK_DAYS)
    out, errors = [], 0
    for brand, channel in YT_CHANNELS.items():
        try:
            xml = _fetch(_yt_feed_url(channel))
        except Exception as e:                       # 채널 단위 실패 격리
            errors += 1; log(f"    유튜브 수집 실패({brand}): {e}"); continue
        for e in _yt_feed_entries(xml)[:YT_PER_CHANNEL]:
            title, vid, published = e["title"], e["videoId"], e["published"]
            if not vid or not title:
                continue
            if not _yt_title_is_relevant(brand, title):
                continue
            try:
                d = dt.datetime.fromisoformat(
                    published.replace("Z", "+00:00")).astimezone(KST).date()
            except Exception:
                continue
            if d < cutoff:
                continue
            out.append(dict(brand=brand, category="social",
                            title=htmllib.unescape(title).strip(),
                            type="유튜브", date=d.isoformat(), collect="auto",
                            url=f"https://www.youtube.com/watch?v={vid}", source="영상"))
    if not out and errors:
        return _seed(category="social", type_in=("유튜브",))
    return out


# --- 유튜브 수집 설정 (채널 RSS) ----------------------------------------------
# 브랜드별 공식 채널 ID. 신세계는 아울렛 전용 채널, 현대/롯데는 아울렛 콘텐츠가
# 포함된 백화점 공식 채널.
YT_CHANNELS = {
    "sse":     "UCppFm0jovWlX-mzTp0iBp5w",   # 신세계사이먼 프리미엄 아울렛
    "hyundai": "UCqaSfiTyF72tSdi5TZ-HHmg",   # THE HYUNDAI 현대백화점
    "lotte":   "UCpBkdV5sGB9v80H8XTewtcg",   # 롯데백화점
}
YT_LOOKBACK_DAYS = 30
YT_PER_CHANNEL = 10

# 채널별 제목 키워드 필터. 신세계사이먼 채널은 이미 아울렛 전용이라 필터가 필요 없다
# (None = 필터 없음, 전부 통과). 현대/롯데는 백화점 통합 채널이라 아울렛과 무관한
# 영상(예: 다른 나라 웹드라마 홍보물 등)이 섞여 나오므로, 브랜드명·"아울렛"·실제
# 아울렛 지점명이 제목에 있어야 통과시킨다. 지점명은 필요에 맞게 조정 가능.
YT_KEYWORD_FILTER = {
    "sse":     None,
    "hyundai": ("아울렛", "outlet", "OUTLET", "김포", "대전", "송도", "스페이스원"),
    "lotte":   ("아울렛", "outlet", "OUTLET", "파주", "이천", "동부산", "광명", "율하", "김해"),
}

def _yt_title_is_relevant(brand: str, title: str) -> bool:
    """채널이 아울렛 전용이 아닌 경우, 제목에 아울렛 관련 키워드가 있는지 검사."""
    kws = YT_KEYWORD_FILTER.get(brand)
    if not kws:
        return True
    tl = title.lower()
    return any(k.lower() in tl for k in kws)

def _yt_feed_url(channel: str) -> str:
    return "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel


# --- 공통: HTTP fetch + RSS/Atom 파싱 (표준 라이브러리) -----------------------
_ATOM = "{http://www.w3.org/2005/Atom}"
_YT_NS = "{http://www.youtube.com/xml/schemas/2015}"

def _fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (outlet-monitor/1.0)"})
    with urlopen(req, timeout=10) as r:
        return r.read()

def _rss_items(xml: bytes) -> List[Dict]:
    """RSS 2.0(구글 뉴스) → item 목록. source 는 매체명."""
    root = ET.fromstring(xml)
    items = []
    for it in root.iterfind(".//item"):
        src = it.find("source")
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "pubDate": (it.findtext("pubDate") or "").strip(),
            "source": (src.text.strip() if src is not None and src.text else None),
        })
    return items

def _yt_feed_entries(xml: bytes) -> List[Dict]:
    """유튜브 채널 Atom 피드 → entry 목록."""
    root = ET.fromstring(xml)
    entries = []
    for e in root.iterfind(f"{_ATOM}entry"):
        entries.append({
            "title": (e.findtext(f"{_ATOM}title") or "").strip(),
            "videoId": (e.findtext(f"{_YT_NS}videoId") or "").strip(),
            "published": (e.findtext(f"{_ATOM}published") or "").strip(),
        })
    return entries

def collect_instagram_manual(path: str = "instagram_manual.json") -> List[Dict]:
    """인스타그램: 반자동(수동 입력). 공식 API 는 본인 계정만 허용하므로
    담당자가 주 1회 각 사 공식 계정 최신 게시물을 아래 파일에 입력하면 병합한다.
      instagram_manual.json = [{brand,title,date,url?}, ...]
    입력값을 검증(브랜드/날짜/제목)하고, 예시·오래된 항목은 걸러낸다.
    파일이 없으면 SEED 의 인스타 항목으로 대체."""
    if not os.path.exists(path):
        return _seed(category="social", type_in=("인스타",))
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log(f"    인스타 입력 파일 오류: {e}")
        return []
    if not isinstance(raw, list):
        return []

    cutoff = NOW.date() - dt.timedelta(days=INSTA_LOOKBACK_DAYS)
    out, skipped = [], 0
    for r in raw:
        try:
            brand = (r.get("brand") or "").strip()
            title = (r.get("title") or "").strip()
            dates = _extract_dates(r.get("date", ""))
            if brand not in BRANDS or not title or not dates:
                skipped += 1; continue
            if any(k in title for k in _INSTA_PLACEHOLDER):   # 템플릿 예시 제외
                skipped += 1; continue
            d = dt.date.fromisoformat(dates[0])
            if d < cutoff:                                    # 오래된 항목 제외
                skipped += 1; continue
            item = dict(brand=brand, category="social", title=title,
                        type="인스타", date=d.isoformat(), collect="semi")
            if r.get("url"):
                item["url"] = r["url"].strip()
            out.append(item)
        except Exception:
            skipped += 1
    if skipped:
        log(f"    인스타 입력: {len(out)}건 반영 · {skipped}건 제외(형식/예시/기간)")
    return out

INSTA_LOOKBACK_DAYS = 30
_INSTA_PLACEHOLDER = ("예시", "여기에", "게시물 제목")   # 템플릿 미수정 항목 감지

def collect_tourism() -> List[Dict]:
    """외국인 동향: 구글 뉴스 RSS (API 키 불필요).
    브랜드명 + 외국인·관광 키워드로 각 사의 외국인 대상 활동·보도를 수집한다."""
    out, errors = _collect_rss("foreign-trends", FOREIGN_QUERY,
                               FOREIGN_LOOKBACK_DAYS, FOREIGN_PER_BRAND,
                               CATEGORY_MODE["foreign-trends"])
    if not out and errors:
        return _seed(category="foreign-trends")
    return out

# --- 외국인 동향 수집 설정 (구글 뉴스 RSS) ------------------------------------
# 브랜드명 + 외국인/관광 관련어(OR)로 조회. 검색어는 필요에 맞게 조정 가능.
_FOREIGN_TERMS = "(외국인 OR 관광 OR 면세 OR 다국어 OR 관광버스)"
FOREIGN_QUERY = {
    "sse":     f"신세계 프리미엄 아울렛 {_FOREIGN_TERMS}",
    "hyundai": f"현대 프리미엄 아울렛 {_FOREIGN_TERMS}",
    "lotte":   f"롯데 프리미엄 아울렛 {_FOREIGN_TERMS}",
}
FOREIGN_LOOKBACK_DAYS = 45   # 외국인 관련 보도는 빈도가 낮아 창을 넓게
FOREIGN_PER_BRAND = 10

# ==========================================================================
# Normalize / Dedup / Diff
# ==========================================================================
def source_link(a: Dict) -> Dict:
    """카테고리·브랜드·유형에 따라 검증용 원문 링크와 출처 라벨을 부여.
    실서비스에서는 크롤러가 수집한 실제 원문 URL 이 이미 a['url'] 에 들어온다."""
    b = BRANDS[a["brand"]]
    if a["category"] == "news":
        from urllib.parse import quote
        return {"source": "뉴스 검색",
                "url": "https://search.naver.com/search.naver?where=news&query=" + quote(a["title"])}
    if a["category"] == "social":
        if a.get("type") == "인스타":
            return {"source": "인스타그램", "url": b["insta"]}
        return {"source": "유튜브", "url": b["home"]}
    if a["category"] == "foreign-trends" and a.get("type") == "관광통계":
        return {"source": "관광 데이터랩", "url": "https://datalab.visitkorea.or.kr/"}
    return {"source": "공식 홈페이지", "url": b["home"]}

def _normalize_title_for_dedupe(title: str) -> str:
    """중복판별용 제목 정규화: 공백 정리 + 흔한 후행 구두점/부호 제거.
    한국어 조사는 형태소분석기 없이 안전하게 뗄 수 없어 건드리지 않는다."""
    t = htmllib.unescape(title or "").strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[\"'“”‘’·\-–—_:;,.!?…\s]+$", "", t)
    return t.strip()

def normalize(a: Dict) -> Dict:
    a.setdefault("collect", "auto")
    if not a.get("url"):
        a.update(source_link(a))
    elif not a.get("source"):
        a["source"] = source_link(a)["source"]
    key = "|".join([a["brand"], a["category"], a["title"],
                    a.get("date") or a.get("start", "")])
    a["id"] = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    # 카테고리 무관 동일기사 식별자: brand + 정규화제목 만으로 계산.
    # 같은 기사가 여러 카테고리(예: promotions·news)에 함께 걸리는 경우, id 는 카테고리가
    # 달라 서로 다르지만 dedupe_key 는 같아진다 → 프론트가 이 값으로 묶어서 중복 노출을 줄일 수 있다.
    # (기존 카테고리 기반 dedup(id)는 그대로 유지하고, 이 필드는 별도로 추가한다.)
    dedupe_src = a["brand"] + "|" + _normalize_title_for_dedupe(a["title"])
    a["dedupe_key"] = hashlib.sha1(dedupe_src.encode("utf-8")).hexdigest()[:12]
    a["collectedAt"] = NOW_ISO
    return a

def dedup(items: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for a in items:
        if a["id"] in seen: continue
        seen.add(a["id"]); out.append(a)
    return out

NEAR_DUP_SEQ_THRESHOLD = 0.82
NEAR_DUP_JACCARD_THRESHOLD = 0.40
NEAR_DUP_JACCARD_MIN_TOKENS = 4

# 조사 제거용 최소 규칙(형태소분석기 없이). 긴 것부터 먼저 검사해 오탐(짧은 조사가 더 긴
# 조사의 일부인 경우) 방지. 1글자 조사는 토큰이 충분히 길 때만 떼어낸다 — 그렇지 않으면
# "증가"→"증"처럼 실제 단어를 훼손할 위험이 커진다.
_PARTICLES_2PLUS = ("에서", "에게", "으로", "까지", "부터", "처럼", "보다",
                    "이라는", "라는", "한테", "이나", "에는", "에도", "와의", "과의")
_PARTICLES_1 = ("은", "는", "이", "가", "을", "를", "의", "도", "만", "에", "로", "와", "과")

def _strip_particle(token: str) -> str:
    for p in _PARTICLES_2PLUS:
        if token.endswith(p) and len(token) > len(p) + 1:
            return token[: -len(p)]
    if len(token) >= 4:
        for p in _PARTICLES_1:
            if token.endswith(p):
                return token[:-1]
    return token

def _tokenize_for_dedupe(title: str) -> set:
    """제목을 한글/영숫자 토큰으로 쪼개고 흔한 조사를 뗀 뒤 2글자 미만은 버린다.
    어순이 달라진 근접 중복(기자마다 다르게 재배열한 헤드라인)을 잡기 위한 용도이며,
    dedupe_key 자체(정규화 문자열 매칭)와는 별개의 보조 신호다."""
    raw = re.findall(r"[가-힣A-Za-z0-9&]+", title)
    return {_strip_particle(t) for t in raw if len(t) >= 2}

def _is_near_duplicate(title_a: str, title_b: str, seq_ratio: float) -> bool:
    """difflib 비율이 임계값 미만이어도, 어순이 바뀐 채 핵심 단어를 충분히 공유하면
    같은 사건으로 본다(자카드 유사도). 토큰 4개 미만인 짧은 제목엔 적용하지 않는다 —
    표본이 작을수록 우연히 겹칠 확률이 높아 오탐 위험이 커지기 때문이다."""
    if seq_ratio >= NEAR_DUP_SEQ_THRESHOLD:
        return True
    ta, tb = _tokenize_for_dedupe(title_a), _tokenize_for_dedupe(title_b)
    if len(ta) < NEAR_DUP_JACCARD_MIN_TOKENS or len(tb) < NEAR_DUP_JACCARD_MIN_TOKENS:
        return False
    union = ta | tb
    if not union:
        return False
    jaccard = len(ta & tb) / len(union)
    return jaccard >= NEAR_DUP_JACCARD_THRESHOLD

def cluster_near_duplicate_keys(items: List[Dict]) -> None:
    """dedupe_key(정확 매칭)로 못 잡는 근접 중복을 같은 브랜드 안에서 두 가지 신호로
    묶는다: ①difflib(문자열 순서 유지된 근접 중복, 예 "...행사" vs "...행사 개최")
    ②토큰 자카드 유사도(어순이 바뀐 근접 중복, 예 언론사마다 절 순서를 재배열한 경우).
    둘 다 표준 라이브러리만 쓴다(외부 의존성 없음). 임계값 이상이면 같은 dedupe_key 로
    합치고, 대표 키는 실행마다 결과가 안정적이도록 사전순으로 가장 작은 키를 쓴다.
    in-place로 items 를 수정한다."""
    from difflib import SequenceMatcher

    by_brand: Dict[str, Dict[str, List[Dict]]] = {}
    for a in items:
        by_brand.setdefault(a["brand"], {}).setdefault(a["dedupe_key"], []).append(a)

    for groups in by_brand.values():
        keys = list(groups.keys())
        titles = {k: _normalize_title_for_dedupe(groups[k][0]["title"]) for k in keys}
        parent = {k: k for k in keys}

        def find(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        def union(ka, kb):
            ra, rb = find(ka), find(kb)
            if ra != rb:
                parent[rb if ra < rb else ra] = ra if ra < rb else rb

        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                ki, kj = keys[i], keys[j]
                if find(ki) == find(kj):
                    continue
                ratio = SequenceMatcher(None, titles[ki], titles[kj]).ratio()
                if _is_near_duplicate(titles[ki], titles[kj], ratio):
                    union(ki, kj)

        for k in keys:
            root = find(k)
            if root != k:
                for a in groups[k]:
                    a["dedupe_key"] = root

def mark_new(items: List[Dict], prev_path: str) -> None:
    """직전 스냅샷(= 이번에 덮어쓸 output 파일) 대비 처음 등장한 항목에 isNew=True.
    최초 실행(스냅샷 없음)에는 최근 3일 내 발생/진행 항목을 신규로 간주."""
    prev_ids = set()
    if os.path.exists(prev_path):
        try:
            with open(prev_path, encoding="utf-8") as f:
                prev_ids = {x["id"] for x in json.load(f).get("activities", [])}
        except Exception:
            pass
    recent = (NOW.date() - dt.timedelta(days=2))
    for a in items:
        if prev_ids:
            a["isNew"] = a["id"] not in prev_ids
        else:
            d = a.get("date") or a.get("start")
            a["isNew"] = bool(d and dt.date.fromisoformat(d) >= recent)

# ==========================================================================
# Pipeline
# ==========================================================================
def run(output_path: str) -> Dict:
    sources, activities = [], []

    def stage(sid, name, fn, collect="auto"):
        try:
            rows = fn()
            activities.extend(rows)
            sources.append({"id": sid, "name": name, "status": collect,
                            "lastRun": NOW_ISO, "note": f"{len(rows)}건"})
            log(f"  · {name}: {len(rows)}건")
        except Exception as e:                       # 부분 실패 허용
            sources.append({"id": sid, "name": name, "status": "error",
                            "lastRun": NOW_ISO, "note": str(e)[:80]})
            log(f"  ! {name}: 실패 - {e}")

    for bk, bv in BRANDS.items():
        # 실제로는 각 사 홈페이지를 크롤링하지 않고 브랜드명 기반 구글 뉴스 RSS 검색으로
        # 대체하고 있으므로, 라벨이 실제 수집 방식을 반영하도록 표기한다.
        stage(f"home-{bk}", f"{bv['label']} 브랜드 뉴스검색(홈페이지 대체)",
              lambda bk=bk: collect_homepage(bk))
    stage("news", "뉴스검색(구글 뉴스 RSS)", collect_news)
    stage("youtube", "유튜브 공식 채널", collect_youtube)
    stage("insta", "인스타그램", collect_instagram_manual, collect="semi")
    stage("tourism", "외국인 동향(뉴스 RSS)", collect_tourism)

    activities = dedup([normalize(a) for a in activities])
    cluster_near_duplicate_keys(activities)           # 근접 중복(제목 유사) dedupe_key 병합
    mark_new(activities, output_path)                # 이전 스냅샷과 비교
    activities.sort(key=lambda a: a.get("date") or a.get("end", ""), reverse=True)

    return {"generatedAt": NOW_ISO, "sources": sources, "activities": activities}


def write_atomic(payload: Dict, output_path: str) -> None:
    """임시 파일에 쓰고 rename 으로 교체 → 대시보드가 반쯤 쓰인 파일을 읽지 않게 보장."""
    d = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, output_path)                     # 원자적 교체


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="경쟁사 현황 수집 → data.json 생성")
    ap.add_argument("--out", default=os.getenv("OUTPUT_PATH", "data.json"),
                    help="출력 파일 경로. A안에서는 웹 접근 경로로 지정 "
                         "(예: /var/www/outlet-monitor/data.json). 환경변수 OUTPUT_PATH 로도 지정 가능.")
    args = ap.parse_args()

    log(f"수집 시작 → {os.path.abspath(args.out)}")
    payload = run(args.out)
    write_atomic(payload, args.out)
    errs = [s for s in payload["sources"] if s["status"] == "error"]
    log(f"완료 · 활동 {len(payload['activities'])}건 · 소스 {len(payload['sources'])}개"
        + (f" · 실패 {len(errs)}개" if errs else ""))
    print(f"data.json 생성 완료 · 활동 {len(payload['activities'])}건 · 소스 {len(payload['sources'])}개")
    sys.exit(1 if errs else 0)                        # 실패 소스가 있으면 비정상 종료(모니터링용)
