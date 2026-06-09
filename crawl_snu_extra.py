from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright


BASE_URL = "https://extra.snu.ac.kr"

START_LIST_URL = (
    "https://extra.snu.ac.kr/ptfol/pgm/index.do?"
    "currentPageNo=1&sort=0001&test=%21%40%23%24%25%5E%26*%28%29_%2B"
    "&listType=&searchDate=&type=&gb="
    "&applyChkCdSh=0001&applyChkCdSh=0002&applyChkCdSh=0004"
    "&planBigCdSh=0000&searchValue=&vshOrgzSh=&eduFrDt=&eduToDt="
    "&userCdCmnSub=0001&schgrCd02CmnSub=0004"
    "&searchIgnoreSh=&detSearch=Y"
)

DETAIL_ENDPOINT = "/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo.do"
VIEW_ENDPOINT = "/ptfol/pgm/view.do"

FIELD_LABELS = [
    "모집상태",
    "운영부서",
    "프로그램 유형",
    "핵심역량",
    "신청대상",
    "신청대상(소속)",
    "신청신분",
    "운영방식",
    "신청기간",
    "활동기간",
    "선발확인일시",
    "선정방법",
    "수료인증서",
    "학적",
    "국적",
    "과정",
    "학년",
    "문의전화",
    "문의메일",
    "홈페이지 주소",
    "장애학생 지원사항",
    "첨부파일",
    "프로그램 주요내용",
]

FOREIGNER_ALLOW_TERMS = [
    "외국인",
    "외국 국적",
    "외국학생",
    "유학생",
    "international",
    "foreigner",
    "foreign student",
]

BROAD_ALLOW_TERMS = [
    "전체",
    "모두",
    "전 구성원",
    "전 재학생",
    "재학생 전체",
    "서울대학교 구성원",
    "제한없음",
    "제한 없음",
]

FOREIGNER_DENY_PATTERNS = [
    r"외국인\s*제외",
    r"유학생\s*제외",
    r"내국인\s*(전용|대상|만)",
    r"한국인\s*(전용|대상|만)",
    r"domestic\s+(students?\s+)?only",
    r"korean\s+(students?\s+)?only",
]

FOREIGNER_RELEVANT_FIELDS = [
    "신청대상",
    "신청대상(소속)",
    "신청신분",
    "국적",
    "프로그램 주요내용",
]

MARKDOWN_COLUMNS = [
    ("Program", "title"),
    ("Status", "모집상태"),
    ("Department", "운영부서"),
    ("Application Period", "신청기간"),
    ("Activity Period", "활동기간"),
    ("Nationality", "국적"),
    ("Homepage", "홈페이지 주소"),
]

NAVIGATION_LINES = {
    "KR",
    "EN",
    "KREN",
    "로그인",
    "로그인하기",
    "SNU 비교과",
    "비교과 프로그램",
    "전체보기",
    "교육 (특강/세미나)",
    "공모전/경진대회",
    "현장학습/인턴",
    "사회공헌 (봉사)",
    "학습/진로상담",
    "레크리에이션",
    "기타",
    "역량·학습유형 진단",
    "SNU 핵심역량진단",
    "SNU 학습유형검사",
    "상담신청",
    "취업컨설팅",
    "학습상담",
    "글쓰기상담",
    "나의 활동",
    "비교과이력",
    "환경설정",
    "안내",
    "공지사항",
    "자료실",
    "설레는 내일을",
    "향한 도전!",
    "여러분의 꿈을 응원합니다.",
    "스크랩",
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def best_program_url(record: dict[str, str]) -> str:
    return normalize_text(record.get("source_url", "") or record.get("url", ""))


def markdown_escape(value: str) -> str:
    return (
        normalize_text(str(value or ""))
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def markdown_url(value: str) -> str:
    return normalize_text(value).replace(" ", "%20").replace(")", "%29")


def markdown_link(label: str, url: str) -> str:
    safe_label = markdown_escape(label) or "Open"
    safe_url = markdown_url(url)

    if not safe_url:
        return safe_label

    return f"[{safe_label}]({safe_url})"


def markdown_cell(record: dict[str, str], field: str) -> str:
    if field == "title":
        return markdown_link(
            record.get("title", "") or "Untitled program",
            best_program_url(record),
        )

    if field == "홈페이지 주소":
        homepage_url = record.get(field, "")
        return markdown_link("Open", homepage_url) if homepage_url else ""

    return markdown_escape(record.get(field, ""))


def write_markdown(records: list[dict[str, str]], path: Path) -> None:
    headers = [header for header, _field in MARKDOWN_COLUMNS]
    lines = [
        "# SNU Extracurricular Programs",
        "",
        f"{len(records)} programs",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _header in headers) + " |",
    ]

    for record in records:
        row = [markdown_cell(record, field) for _header, field in MARKDOWN_COLUMNS]
        lines.append("| " + " | ".join(row) + " |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_page_url(list_url: str, page_no: int) -> str:
    """
    Replaces currentPageNo while preserving duplicate query keys,
    for example repeated applyChkCdSh parameters.
    """
    parts = urlsplit(list_url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)

    replaced = False
    new_pairs = []

    for key, value in query_pairs:
        if key == "currentPageNo":
            new_pairs.append((key, str(page_no)))
            replaced = True
        else:
            new_pairs.append((key, value))

    if not replaced:
        new_pairs.append(("currentPageNo", str(page_no)))

    new_query = urlencode(new_pairs, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def extract_detail_urls_from_html(html: str) -> list[str]:
    """
    Looks for detail URLs in href, onclick, JavaScript snippets, and raw HTML.
    This is intentionally broad because university sites often attach links
    through onclick handlers rather than clean <a href="..."> tags.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()

    searchable_chunks: list[str] = [html]

    for tag in soup.find_all(True):
        href = tag.get("href")
        onclick = tag.get("onclick")
        data_attrs = " ".join(
            str(value)
            for key, value in tag.attrs.items()
            if key.startswith("data-")
        )

        for chunk in [href, onclick, data_attrs]:
            if chunk:
                searchable_chunks.append(str(chunk))

    for chunk in searchable_chunks:
        for match in re.finditer(
            r"(?P<url>/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo\.do\?[^'\"\s<>)]*)",
            chunk,
        ):
            urls.add(urljoin(BASE_URL, match.group("url")))

        for match in re.finditer(
            r"(?P<url>https://extra\.snu\.ac\.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo\.do\?[^'\"\s<>)]*)",
            chunk,
        ):
            urls.add(match.group("url"))

        for seq in re.findall(r"encSddpbSeq\s*[=:]\s*['\"]?([a-fA-F0-9]{32})", chunk):
            urls.add(f"{BASE_URL}{DETAIL_ENDPOINT}?currentPageNo=&encSddpbSeq={seq}")

        for seq in re.findall(r"encSddpbSeq=([a-fA-F0-9]{32})", chunk):
            urls.add(f"{BASE_URL}{DETAIL_ENDPOINT}?currentPageNo=&encSddpbSeq={seq}")

        for seq, path in re.findall(
            r"global\.write\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            chunk,
        ):
            urls.add(urljoin(BASE_URL, f"{path}?dataSeq={seq}"))

    return sorted(urls)


def wait_past_snu_wait_page(page: Page, max_wait_seconds: int = 15) -> None:
    """
    Some detail pages may first redirect to wait.jsp.
    This waits briefly for the real detail page to load.
    """
    deadline = time.time() + max_wait_seconds

    while time.time() < deadline:
        current_url = page.url.lower()
        body_text = ""

        try:
            body_text = page.locator("body").inner_text(timeout=2000)
        except Exception:
            pass

        if "wait.jsp" not in current_url and len(normalize_text(body_text)) > 200:
            return

        page.wait_for_timeout(1000)


def extract_title(lines: list[str]) -> str:
    """
    Fallback title extractor.

    The exact title selector may change, so this chooses the first line that
    does not look like global navigation.
    """
    for index, line in enumerate(lines):
        if line == "스크랩" and index + 1 < len(lines):
            return lines[index + 1]

    for line in lines:
        if line not in NAVIGATION_LINES and not line.startswith("정원 ") and len(line) >= 8:
            return line

    return ""


def extract_label_value(lines: list[str], label: str, all_labels: Iterable[str]) -> str:
    """
    Extracts text after a Korean label.

    It supports both:
        신청기간 2026.06.09.~2026.06.17.

    and:
        신청기간
        2026.06.09.~2026.06.17.
    """
    label_set = set(all_labels)

    for index, line in enumerate(lines):
        if line == label:
            rest = ""
        elif line.startswith(label):
            suffix = line[len(label) :]
            if suffix and suffix[0] not in " :：\t":
                continue
            rest = suffix.strip(" :：")
        else:
            continue

        if rest:
            return rest

        collected = []

        for next_line in lines[index + 1 :]:
            if next_line in label_set:
                break

            if any(next_line.startswith(other) for other in label_set if other != label):
                break

            collected.append(next_line)

            if label != "프로그램 주요내용" and len(collected) >= 3:
                break

        return normalize_text(" ".join(collected))

    return ""


def foreigner_filter_text(record: dict[str, str]) -> str:
    return normalize_text(" ".join(record.get(field, "") for field in FOREIGNER_RELEVANT_FIELDS))


def foreigner_match_reason(record: dict[str, str], *, include_broad_all: bool = True) -> str:
    nationality = normalize_text(record.get("국적", ""))
    lowered_nationality = nationality.lower()

    if nationality:
        for pattern in FOREIGNER_DENY_PATTERNS:
            if re.search(pattern, lowered_nationality, flags=re.IGNORECASE):
                return ""

        if any(term.lower() in lowered_nationality for term in FOREIGNER_ALLOW_TERMS):
            return f"nationality: {nationality}"

        if include_broad_all and any(term.lower() in lowered_nationality for term in BROAD_ALLOW_TERMS):
            return f"nationality: {nationality}"

        if "내국인" in nationality:
            return ""

    text = foreigner_filter_text(record)
    lowered = text.lower()

    if not text:
        return ""

    for pattern in FOREIGNER_DENY_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return ""

    for term in FOREIGNER_ALLOW_TERMS:
        if term.lower() in lowered:
            return f"matched: {term}"

    if include_broad_all:
        for term in BROAD_ALLOW_TERMS:
            if term.lower() in lowered:
                return f"broadly eligible: {term}"

    return ""


def allows_foreigners(record: dict[str, str], *, include_broad_all: bool = True) -> bool:
    return bool(foreigner_match_reason(record, include_broad_all=include_broad_all))


def parse_detail_page(page: Page, url: str) -> dict[str, str]:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    wait_past_snu_wait_page(page)
    page.wait_for_load_state("networkidle", timeout=15000)

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    body_text = soup.get_text("\n")
    lines = [
        normalize_text(line)
        for line in body_text.splitlines()
        if normalize_text(line)
    ]

    result = {
        "url": page.url,
        "source_url": url,
        "title": extract_title(lines),
    }

    for label in FIELD_LABELS:
        result[label] = extract_label_value(lines, label, FIELD_LABELS)

    result["allows_foreigners"] = str(allows_foreigners(result))
    result["allows_foreigners_reason"] = foreigner_match_reason(result)

    return result


def crawl(
    max_pages: int,
    delay_seconds: float,
    headless: bool,
    foreigner_only: bool,
    strict_foreigner_match: bool,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen_detail_urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        for page_no in range(1, max_pages + 1):
            list_url = build_page_url(START_LIST_URL, page_no)
            print(f"[LIST] {page_no}: {list_url}")

            page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=15000)

            html = page.content()
            detail_urls = extract_detail_urls_from_html(html)

            if not detail_urls:
                print(f"[WARN] No detail URLs found on listing page {page_no}.")
                print("[WARN] The site may hide detail IDs behind JavaScript events.")
                break

            new_urls = [url for url in detail_urls if url not in seen_detail_urls]

            if not new_urls:
                print(f"[STOP] No new detail URLs on page {page_no}.")
                break

            for detail_url in new_urls:
                seen_detail_urls.add(detail_url)
                print(f"[DETAIL] {detail_url}")

                try:
                    record = parse_detail_page(page, detail_url)
                    reason = foreigner_match_reason(
                        record,
                        include_broad_all=not strict_foreigner_match,
                    )
                    record["allows_foreigners"] = str(bool(reason))
                    record["allows_foreigners_reason"] = reason

                    if foreigner_only and not reason:
                        print(f"[SKIP] Not foreigner-eligible: {record.get('title', detail_url)}")
                        continue

                    records.append(record)
                except Exception as error:
                    if not foreigner_only:
                        records.append(
                            {
                                "url": detail_url,
                                "error": repr(error),
                                "allows_foreigners": "False",
                                "allows_foreigners_reason": "",
                            }
                        )

                time.sleep(delay_seconds)

        browser.close()

    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--out-json", default="snu_extra_programs.json")
    parser.add_argument("--out-csv", default="snu_extra_programs.csv")
    parser.add_argument("--out-md", default="snu_extra_programs.md")
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Write all programs instead of only programs that allow foreign students.",
    )
    parser.add_argument(
        "--strict-foreigner-match",
        action="store_true",
        help="Require explicit foreigner/international-student wording; do not treat broad 'all students' wording as eligible.",
    )
    args = parser.parse_args()

    records = crawl(
        max_pages=args.max_pages,
        delay_seconds=args.delay,
        headless=not args.headed,
        foreigner_only=not args.include_all,
        strict_foreigner_match=args.strict_foreigner_match,
    )

    json_path = Path(args.out_json)
    csv_path = Path(args.out_csv)
    md_path = Path(args.out_md)

    json_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )

    pd.DataFrame(records).to_csv(
        csv_path,
        index=False,
        encoding="utf-8-sig",
    )

    write_markdown(records, md_path)

    print(f"[DONE] Saved {len(records)} records")
    print(f"[DONE] JSON: {json_path}")
    print(f"[DONE] CSV: {csv_path}")
    print(f"[DONE] Markdown: {md_path}")


if __name__ == "__main__":
    main()
