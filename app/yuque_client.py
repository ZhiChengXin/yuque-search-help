from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any
from urllib.parse import quote, unquote

import httpx
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from .models import (
    ContactRecord,
    DocSection,
    contacts_to_sections,
    parse_lakesheet_contacts,
    split_anchor_markdown,
)

logger = logging.getLogger("yuque-help-search")

YUQUE_PWD_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCfwyOyncSrUTmkaUPsXT6UUdXx
TQ6a0wgPShvebfwq8XeNj575bUlXxVa/ExIn4nOUwx6iR7vJ2fvz5Ls750D051S7
q70sevcmc8SsBNoaMQtyF/gETPBSsyWv3ccBJFrzZ5hxFdlVUfg6tXARtEI8rbIH
su6TBkVjk+n1Pw/ihQIDAQAB
-----END PUBLIC KEY-----"""

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/20G81 "
    "YuqueMobileApp/1.0.2 (AppBuild/650 Device/Phone Locale/zh-cn "
    "Theme/light YuqueType/public)"
)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 "
    "YuqueHelpSearch/1.0"
)


def encrypt_password(password: str) -> str:
    key = RSA.import_key(YUQUE_PWD_PUBLIC_KEY.encode())
    cipher = PKCS1_v1_5.new(key)
    payload = f"{int(time.time() * 1000)}:{password}".encode()
    encrypted = cipher.encrypt(payload)
    return base64.b64encode(encrypted).decode()


class YuqueClient:
    def __init__(
        self,
        space_login: str,
        repo: str,
        host: str = "https://www.yuque.com",
        token: str = "",
        username: str = "",
        password: str = "",
    ) -> None:
        self.space_login = space_login
        self.repo = repo
        self.host = host.rstrip("/")
        self.token = (token or "").strip()
        self.username = username
        self.password = password
        self.namespace = f"{space_login}/{repo}"
        self.book_id: str | None = None
        headers = {"User-Agent": BROWSER_UA}
        if self.token:
            headers["X-Auth-Token"] = self.token
        self._client = httpx.Client(
            timeout=60.0,
            follow_redirects=True,
            headers=headers,
        )
        self._logged_in = bool(self.token)

    def close(self) -> None:
        self._client.close()

    def login(self) -> None:
        if self.token:
            self._logged_in = True
            return
        if not self.username or not self.password:
            raise RuntimeError("未配置 YUQUE_TOKEN，也未提供账号密码")
        encrypted = encrypt_password(self.password)
        url = f"{self.host}/api/mobile_app/accounts/login?language=zh-cn"
        resp = self._client.post(
            url,
            json={
                "login": self.username,
                "password": encrypted,
                "loginType": "password",
            },
            headers={
                "User-Agent": MOBILE_UA,
                "Referer": f"{self.host}/login?goto={quote(self.host + '/dashboard')}",
                "Origin": self.host,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"语雀登录失败 HTTP {resp.status_code}: {resp.text[:300]}")
        if not self._client.cookies:
            raise RuntimeError("语雀登录未返回会话 Cookie")
        self._logged_in = True

    def ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.ensure_login()
        url = path if path.startswith("http") else f"{self.host}/{path.lstrip('/')}"
        resp = self._client.request(method, url, **kwargs)
        if resp.status_code == 401 and not self.token:
            self._logged_in = False
            self.login()
            resp = self._client.request(method, url, **kwargs)
        return resp

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._request("GET", path, params=params)
        if resp.status_code != 200:
            raise RuntimeError(
                f"语雀接口失败 {path}: HTTP {resp.status_code} {resp.text[:240]}"
            )
        payload = resp.json()
        return payload.get("data", payload)

    def fetch_book_meta(self) -> dict[str, Any]:
        if self.token:
            repo = self._get_json(f"api/v2/repos/{self.namespace}")
            self.book_id = str(repo.get("id") or "")
            toc = []
            try:
                toc_data = self._get_json(f"api/v2/repos/{self.namespace}/toc")
                toc = toc_data if isinstance(toc_data, list) else (toc_data.get("toc") or [])
            except Exception:
                logger.warning("读取知识库 TOC 失败，将不带目录路径")
            if not self.book_id:
                raise RuntimeError("未能解析知识库 book_id")
            return {"book": repo, "toc": toc}

        resp = self._request("GET", f"{self.host}/{self.namespace}")
        if resp.status_code != 200:
            raise RuntimeError(f"无法打开知识库页面: HTTP {resp.status_code}")
        html = resp.text
        match = re.search(
            r"window\.appData\s*=\s*JSON\.parse\(decodeURIComponent\(\"([^\"]+)\"\)\)",
            html,
        )
        if not match:
            raise RuntimeError("未能从知识库页面解析 appData")
        app_data = json.loads(unquote(match.group(1)))
        book = app_data.get("book") or {}
        self.book_id = str(book.get("id") or "")
        if not self.book_id:
            raise RuntimeError("未能解析知识库 book_id")
        return {"book": book, "toc": book.get("toc") or []}

    def list_docs(self) -> list[dict[str, Any]]:
        meta = self.fetch_book_meta()
        toc = meta.get("toc") or []
        toc_map = {
            item.get("url") or item.get("slug"): item
            for item in toc
            if isinstance(item, dict) and str(item.get("type") or "").upper() == "DOC"
        }
        if self.token:
            docs: list[dict[str, Any]] = []
            offset = 0
            while True:
                batch = self._get_json(
                    f"api/v2/repos/{self.namespace}/docs",
                    params={"offset": offset, "limit": 100},
                )
                if not isinstance(batch, list):
                    batch = (batch or {}).get("docs") or []
                docs.extend(batch)
                if len(batch) < 100:
                    break
                offset += 100
        else:
            docs = self._get_json("api/docs", params={"book_id": self.book_id})
            if not isinstance(docs, list):
                docs = docs.get("docs") or []

        enriched = []
        for doc in docs:
            slug = doc.get("slug") or ""
            toc_item = toc_map.get(slug)
            path = self._resolve_catalog_path(toc, toc_item) if toc_item else []
            enriched.append({**doc, "_catalog_path": path})
        return enriched

    def _resolve_catalog_path(
        self, toc: list[dict[str, Any]], item: dict[str, Any]
    ) -> list[str]:
        by_uuid = {x.get("uuid"): x for x in toc if isinstance(x, dict)}
        titles: list[str] = []
        parent_id = item.get("parent_uuid")
        level = int(item.get("level") or 0)
        for _ in range(max(level, 0)):
            parent = by_uuid.get(parent_id)
            if not parent:
                break
            title = (parent.get("title") or "").strip()
            if title:
                titles.append(title)
            parent_id = parent.get("parent_uuid")
        return list(reversed(titles))

    def fetch_anchor_markdown(self, slug: str) -> str:
        resp = self._request(
            "GET",
            f"{self.host}/{self.namespace}/{slug}/markdown",
            params={"attachment": "true", "latexcode": "false", "anchor": "true"},
        )
        if resp.status_code == 200 and resp.text and "<!doctype html>" not in resp.text[:80].lower():
            return resp.text
        return ""

    def fetch_doc_detail(self, doc_id_or_slug: str) -> dict[str, Any]:
        if self.token:
            return self._get_json(
                f"api/v2/repos/{self.namespace}/docs/{doc_id_or_slug}",
                params={"raw": "1"},
            )
        return self._get_json(
            f"api/docs/{doc_id_or_slug}",
            params={"book_id": self.book_id, "raw": "1"},
        )

    def fetch_all(self, concurrency: int = 4) -> tuple[list[DocSection], list[ContactRecord]]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        docs = self.list_docs()
        sections: list[DocSection] = []
        contacts: list[ContactRecord] = []

        def load_one(doc: dict[str, Any]) -> tuple[list[DocSection], list[ContactRecord]]:
            slug = doc.get("slug") or ""
            doc_id = str(doc.get("id") or slug)
            title = (doc.get("title") or "未命名文档").strip()
            catalog_path = doc.get("_catalog_path") or []
            updated_at = str(doc.get("updated_at") or "")
            doc_url = f"{self.host}/{self.namespace}/{slug}"
            detail = self.fetch_doc_detail(slug or doc_id)
            fmt = (doc.get("format") or detail.get("format") or "").lower()

            if fmt == "lakesheet":
                body = detail.get("body") or detail.get("body_sheet") or detail.get("content") or ""
                parsed = parse_lakesheet_contacts(body, source_url=doc_url)
                contact_sections = contacts_to_sections(
                    parsed,
                    doc_id=doc_id,
                    slug=slug,
                    doc_title=title,
                    doc_url=doc_url,
                )
                return contact_sections, parsed

            raw = self.fetch_anchor_markdown(slug)
            if not raw:
                raw = detail.get("body_html") or detail.get("body") or ""
            split = split_anchor_markdown(
                raw,
                doc_id=doc_id,
                slug=slug,
                doc_title=title,
                doc_url=doc_url,
                catalog_path=catalog_path,
                updated_at=updated_at,
            )
            return split, []

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(load_one, d) for d in docs]
            for fut in as_completed(futures):
                secs, cons = fut.result()
                sections.extend(secs)
                contacts.extend(cons)

        sections.sort(key=lambda x: (x.doc_title, x.section_title))
        return sections, contacts

    def get_doc(self, namespace: str, slug: str) -> dict[str, Any]:
        return self._get_json(f"api/v2/repos/{namespace}/docs/{slug}", params={"raw": "1"})

    def update_doc_markdown(self, namespace: str, slug: str, title: str, body: str) -> dict[str, Any]:
        resp = self._request(
            "PUT",
            f"api/v2/repos/{namespace}/docs/{slug}",
            json={"title": title, "body": body, "format": "markdown", "_force_asl": 1},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"更新语雀文档失败 HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        return payload.get("data", payload)

    def upload_image(
        self,
        filename: str,
        content: bytes,
        mime: str = "image/png",
        doc_id: str = "",
        *,
        allow_data_uri: bool = True,
    ) -> str:
        data: dict[str, str] = {"type": "image"}
        if doc_id:
            data["attachable_type"] = "Doc"
            data["attachable_id"] = str(doc_id)
        resp = self._request(
            "POST",
            "api/v2/upload/attach",
            files={"file": (filename, content, mime)},
            data=data,
        )
        if resp.status_code in (200, 201):
            payload = resp.json()
            item = payload.get("data", payload) if isinstance(payload, dict) else {}
            url = str(
                item.get("url")
                or item.get("src")
                or (item.get("links") or {}).get("url")
                or ""
            ).strip()
            if url:
                return url
            raise RuntimeError(f"上传截图未返回地址: {str(payload)[:240]}")
        if resp.status_code == 401 and "attach_upload" in (resp.text or ""):
            if not allow_data_uri:
                raise RuntimeError(
                    "上传截图失败：Token 缺少图集权限（attach_upload），请勾选「修改和读取你的图集」"
                )
            encoded = base64.b64encode(content).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        raise RuntimeError(f"上传截图失败 HTTP {resp.status_code}: {resp.text[:300]}")
