"""Verify the hardened fetch_url tool against real, live URLs.

Ad-hoc verification script for the MODERATE-risk-tier / content-type-sniffing
/ byte-cap changes to jarvis.tools.web.fetch_url. Not part of the permanent
suite naming scheme necessarily, but follows the same check() pattern.

    uv run python scripts/check_fetch_url_hardening.py
"""

from __future__ import annotations

import asyncio
import sys

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def main() -> int:
    from jarvis.security import Risk
    from jarvis.tools import registry

    # Import web module to ensure fetch_url is registered.
    import jarvis.tools.web  # noqa: F401

    print("\n[1] HTML page\n")
    result = await registry.invoke("fetch_url", {"url": "https://example.com"})
    content = result.get("content") or ""
    check(
        "fetch_url on example.com succeeds with non-empty cleaned content",
        result.get("ok", True) and len(content) > 0,
        f"ok={result.get('ok')!r} title={result.get('title')!r} content[:120]={content[:120]!r}",
    )
    check(
        "title populated",
        bool(result.get("title")),
        f"title={result.get('title')!r}",
    )

    print("\n[4] risk tier\n")
    risk = registry.REGISTRY["fetch_url"].risk
    check("fetch_url risk is Risk.MODERATE", risk == Risk.MODERATE, f"risk={risk!r}")

    print("\n[2] JSON API endpoint\n")
    result = await registry.invoke("fetch_url", {"url": "https://httpbin.org/json"})
    content = result.get("content") or ""
    check(
        "fetch_url on httpbin.org/json returns raw JSON verbatim, not rejected",
        result.get("ok", True) and content.strip().startswith("{"),
        f"ok={result.get('ok')!r} content[:200]={content[:200]!r}",
    )

    print("\n[3] non-text content type (PDF)\n")
    result = await registry.invoke(
        "fetch_url",
        {"url": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"},
    )
    error = result.get("error") or ""
    check(
        "fetch_url rejects PDF with ok:false and a content-type-mentioning error",
        result.get("ok") is False and ("pdf" in error.lower() or "not a readable" in error.lower()),
        f"result={result!r}",
    )

    print("\n[5] max_chars truncation still works\n")
    # example.com's full cleaned text is only ~142 chars, so use a cap well
    # below that to actually force truncation (200 would be a no-op here).
    result = await registry.invoke("fetch_url", {"url": "https://example.com", "max_chars": 50})
    content = result.get("content") or ""
    check(
        "small max_chars truncates content and sets truncated:true",
        result.get("truncated") is True and len(content) <= 50,
        f"len(content)={len(content)} truncated={result.get('truncated')!r} content={content!r}",
    )

    print("\n[6] unreachable URL fails gracefully\n")
    result = await registry.invoke(
        "fetch_url", {"url": "https://this-domain-does-not-exist-12345.invalid"}
    )
    check(
        "unreachable domain returns ok:false with error, does not raise",
        result.get("ok") is False and bool(result.get("error")),
        f"result={result!r}",
    )

    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
