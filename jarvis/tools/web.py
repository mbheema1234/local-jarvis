"""Web access: searching, reading pages, and opening things in the browser.

Two search paths, deliberately distinct:

``search_web`` hits DuckDuckGo and returns raw results. It is free and fast, and
the snippets usually contain the answer outright.

``research`` asks a search-native model (Perplexity Sonar via OpenRouter) an
actual question and gets a synthesised answer with sources. It costs about half
a cent per call, so it is reserved for questions that need reasoning across
several sources rather than a lookup.
"""

from __future__ import annotations

import re
import urllib.parse

import httpx

from ..appindex import index
from ..config import load
from ..log import get
from ..security import Risk
from .registry import tool

log = get("jarvis.tools.web")


def _normalise_url(url: str) -> str:
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return "https://" + url.lstrip("/")
    return url


# --------------------------------------------------------------------------
# Searching
# --------------------------------------------------------------------------


@tool(
    risk=Risk.SAFE,
    params={
        "query": "What to search for. Use plain keywords, as you would type into a search box.",
        "max_results": "How many results to return (1-10).",
    },
    summary=lambda a: f"Search the web for {a.get('query', '?')!r}",
    tags=["web"],
)
def search_web(query: str, max_results: int = 5) -> dict:
    """Search the web and read the results.

    Use this whenever you need current information, or anything you are not
    certain about: news, prices, release dates, sports results, documentation,
    "what is X". Answer from the snippets when they are sufficient; call
    fetch_url on a result if you need the full page.
    """
    from ddgs import DDGS

    count = max(1, min(int(max_results), 10))
    try:
        raw = DDGS().text(query, max_results=count)
    except Exception as exc:
        log.warning("web search failed: %s", exc)
        return {
            "ok": False,
            "error": f"Search is unavailable right now ({type(exc).__name__}). "
                     f"Try the research tool instead.",
        }

    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "snippet": (item.get("body") or "")[:400],
        }
        for item in raw
    ]
    if not results:
        return {"ok": False, "error": f"No results for {query!r}."}
    return {"query": query, "count": len(results), "results": results}


@tool(
    risk=Risk.SAFE,
    params={
        "query": "Topic to find recent news about.",
        "max_results": "How many articles to return (1-10).",
    },
    summary=lambda a: f"Search news for {a.get('query', '?')!r}",
    tags=["web"],
)
def search_news(query: str, max_results: int = 5) -> dict:
    """Search recent news articles.

    Prefer this over search_web when the user asks what is happening, what is
    new, or about anything time-sensitive.
    """
    from ddgs import DDGS

    count = max(1, min(int(max_results), 10))
    try:
        raw = DDGS().news(query, max_results=count)
    except Exception as exc:
        log.warning("news search failed: %s", exc)
        return {"ok": False, "error": f"News search unavailable ({type(exc).__name__})."}

    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": item.get("source", ""),
            "date": (item.get("date") or "")[:10],
            "snippet": (item.get("body") or "")[:300],
        }
        for item in raw
    ]
    if not results:
        return {"ok": False, "error": f"No news found for {query!r}."}
    return {"query": query, "count": len(results), "results": results}


@tool(
    risk=Risk.SAFE,
    params={
        "question": "The full question to research, phrased as a complete sentence.",
    },
    summary=lambda a: f"Research: {(a.get('question') or '')[:70]}",
    tags=["web"],
)
async def research(question: str) -> dict:
    """Ask a search-powered model a question and get a sourced answer.

    Use this for questions that need several sources pulled together, compared,
    or reasoned about -- not for simple lookups, where search_web is free and
    faster. Costs roughly half a cent per call.
    """
    from ..llm.openrouter import OpenRouterError, client

    messages = [
        {
            "role": "system",
            "content": (
                "Answer the question directly and concisely, in two or three "
                "sentences of plain prose. Your answer will be read aloud, so "
                "use no markdown, no bullet points, and no citation brackets."
            ),
        },
        {"role": "user", "content": question},
    ]

    try:
        result = await client.chat(
            messages,
            model=load().models.search,
            max_tokens=700,
            temperature=0.2,
        )
    except OpenRouterError as exc:
        return {
            "ok": False,
            "error": f"Research unavailable: {exc}. Try search_web instead.",
        }

    message = result.get("message") or {}
    answer = (message.get("content") or "").strip()

    # OpenRouter surfaces Sonar's sources either as a top-level citations list
    # or as url_citation annotations, depending on the upstream response.
    sources: list[str] = []
    for annotation in message.get("annotations") or []:
        citation = annotation.get("url_citation") or {}
        if citation.get("url"):
            sources.append(citation["url"])
    for url in message.get("citations") or []:
        if isinstance(url, str):
            sources.append(url)

    return {
        "question": question,
        "answer": answer,
        "sources": list(dict.fromkeys(sources))[:6],
        "model": result.get("model"),
    }


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@tool(
    risk=Risk.SAFE,
    params={"url": "Page to fetch and read."},
    summary=lambda a: f"Read {a.get('url', '?')}",
    tags=["web"],
)
async def fetch_url(url: str, max_chars: int = 5000) -> dict:
    """Fetch a web page and return its text, so you can answer from its content.

    Use after search_web when a snippet is not enough and you need the detail
    on the page itself.
    """
    url = _normalise_url(url)
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis/0.1"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not fetch {url}: {exc}"}

    # Crude but dependency-free readability: drop non-content elements, strip
    # the remaining tags, and collapse whitespace.
    text = re.sub(r"(?is)<(script|style|nav|footer|header|svg)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&(nbsp|amp|lt|gt|quot|#39);", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return {
        "url": url,
        "title": title_match.group(1).strip() if title_match else "",
        "content": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


# --------------------------------------------------------------------------
# Opening things in the browser
# --------------------------------------------------------------------------


@tool(
    risk=Risk.MODERATE,
    params={"url": "The URL to open in the default browser."},
    summary=lambda a: f"Open {a.get('url', '?')}",
    tags=["web"],
)
def open_url(url: str) -> dict:
    """Open a URL in the default browser, for the user to look at."""
    url = _normalise_url(url)
    index.launch_path(url)
    return {"opened": url}


@tool(
    risk=Risk.MODERATE,
    params={"query": "What to search for.", "engine": "Which search engine to use."},
    summary=lambda a: f"Open a browser search for {a.get('query', '?')!r}",
    tags=["web"],
)
def open_web_search(query: str, engine: str = "google") -> dict:
    """Open a search in the browser for the user to look through themselves.

    This shows results on screen; it does not let you read them. When you need
    the answer yourself, use search_web instead.
    """
    templates = {
        "google": "https://www.google.com/search?q={}",
        "bing": "https://www.bing.com/search?q={}",
        "duckduckgo": "https://duckduckgo.com/?q={}",
        "youtube": "https://www.youtube.com/results?search_query={}",
    }
    template = templates.get(engine.lower(), templates["google"])
    url = template.format(urllib.parse.quote_plus(query))
    index.launch_path(url)
    return {"searched": query, "url": url, "note": "Opened in the browser."}
