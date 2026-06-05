"""arXiv adapter — Atom API, no key (plan/8). Lazy httpx; stdlib XML parse."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

from research_council.store.models import Paper

ARXIV = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
# arXiv asks for a descriptive User-Agent and a delay between requests.
_HEADERS = {"User-Agent": "research-council/0.0.1 (https://github.com/; mailto:research@example.org)"}


class ArxivProvider:
    name = "arxiv"

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        import httpx

        params = {"search_query": f"all:{query}", "start": 0, "max_results": min(k, 25)}
        xml = ""
        try:
            async with httpx.AsyncClient(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
                for attempt in range(2):  # one polite retry on rate-limit
                    r = await c.get(ARXIV, params=params)
                    if r.status_code == 429 and attempt == 0:
                        await asyncio.sleep(3)
                        continue
                    r.raise_for_status()
                    xml = r.text
                    break
        except Exception:
            return []
        if not xml:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        out: list[Paper] = []
        for e in root.findall(f"{ATOM}entry")[:k]:
            idu = (e.findtext(f"{ATOM}id") or "").strip()
            pub = (e.findtext(f"{ATOM}published") or "")[:4]
            out.append(Paper(
                id=idu.rsplit("/", 1)[-1] or idu or "(arxiv)",
                title=" ".join((e.findtext(f"{ATOM}title") or "").split()),
                abstract=" ".join((e.findtext(f"{ATOM}summary") or "").split()),
                year=int(pub) if pub.isdigit() else None,
                url=idu or None,
                source="arxiv",
            ))
        return out
