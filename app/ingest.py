import hashlib
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import tiktoken
import urllib3
from bs4 import BeautifulSoup

MAX_BYTES = 30 * 1024 * 1024


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("인증 정보가 없는 http/https URL을 입력해주세요.")
    if parts.port not in {None, 80, 443}:
        raise ValueError("웹 수집은 80/443 포트만 지원합니다.")
    addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("웹 수집은 공개 인터넷 주소만 허용합니다.")
    return url


def fetch_url(url):
    public_url(url)
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    # Pin the actual connection to a validated address, preventing DNS rebinding.
    addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("웹 수집은 공개 인터넷 주소만 허용합니다.")
    address = addresses[0][4][0]
    hostname = parts.hostname.encode("idna").decode()
    options = {"timeout": urllib3.Timeout(connect=5, read=20), "retries": False}
    pool = (
        urllib3.HTTPSConnectionPool(
            address,
            port,
            server_hostname=hostname,
            assert_hostname=hostname,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            **options,
        )
        if parts.scheme == "https"
        else urllib3.HTTPConnectionPool(address, port, **options)
    )
    target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    with pool:
        response = pool.urlopen(
            "GET",
            target,
            headers={"Host": hostname, "User-Agent": "ShelfRAG/0.1"},
            redirect=False,
            preload_content=False,
        )
        try:
            if 300 <= response.status < 400:
                raise ValueError("이동된 주소입니다. 브라우저의 최종 URL을 등록해주세요.")
            if response.status != 200:
                raise ValueError(f"웹페이지 응답 오류: HTTP {response.status}")
            if "html" not in response.headers.get("content-type", ""):
                raise ValueError("HTML 웹페이지를 입력해주세요. PDF는 파일로 업로드해주세요.")
            data = bytearray()
            for block in response.stream(65536, decode_content=True):
                data.extend(block)
                if len(data) > MAX_BYTES:
                    raise ValueError("웹페이지가 30MB 제한을 초과했습니다.")
            return bytes(data)
        finally:
            response.close()


def html_text(data):
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup
    return main.get_text("\n", strip=True)


def extract(path: Path, kind: str):
    if kind == "url":
        return [("웹 본문", html_text(path.read_bytes()))]
    if kind == "pdf":
        from pypdf import PdfReader

        pages = [(f"p.{i + 1}", p.extract_text() or "") for i, p in enumerate(PdfReader(path).pages)]
        if any(len(text.strip()) < 20 for _, text in pages):
            if os.getenv("OCR_ENABLED", "false").lower() != "true":
                raise ValueError(
                    "텍스트가 없는 페이지가 있습니다. 스캔 PDF는 OCR_ENABLED=true와 Tesseract가 필요합니다."
                )
            import pymupdf

            with pymupdf.open(path) as doc:
                pages = [
                    (
                        label,
                        text
                        if len(text.strip()) >= 20
                        else doc[i].get_text(
                            textpage=doc[i].get_textpage_ocr(
                                language=os.getenv("OCR_LANGUAGE", "kor+eng"), full=True
                            )
                        ),
                    )
                    for i, (label, text) in enumerate(pages)
                ]
        return pages
    if kind == "epub":
        import zipfile

        import ebooklib
        from ebooklib import epub

        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("EPUB 압축 해제 크기는 100MB 이하여야 합니다.")
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
        return [
            (item.get_name(), html_text(item.get_content()))
            for item_id, _ in book.spine
            if (item := book.get_item_with_id(item_id)) and item.get_type() == ebooklib.ITEM_DOCUMENT
        ]
    return [("본문", path.read_text(encoding="utf-8-sig"))]


def chunks(sections, size=700, overlap=80):
    if not 0 <= overlap < size:
        raise ValueError("Invalid overlap")
    encoder = tiktoken.get_encoding("cl100k_base")
    result = []
    for location, text in sections:
        tokens = encoder.encode(text, disallowed_special=())
        for start in range(0, len(tokens), size - overlap):
            body = encoder.decode(tokens[start : start + size]).strip()
            if body:
                result.append({"location": location, "text": body, "ordinal": len(result)})
            if start + size >= len(tokens):
                break
    if not result:
        raise ValueError("추출 가능한 본문이 없습니다.")
    return result


def version_id(source_id, raw):
    return hashlib.sha256(source_id.encode() + raw).hexdigest()
