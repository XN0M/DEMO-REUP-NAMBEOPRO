import asyncio
import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional
from playwright.async_api import async_playwright
from core.extractor_fb import FacebookExtractor
from core.extractor_yt import YouTubeExtractor

class DouyinExtractor:
    def __init__(self):
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        self.fb_extractor = FacebookExtractor()
        self.yt_extractor = YouTubeExtractor()

    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video/modal ID from various Douyin URL patterns."""
        # Pattern 1: modal_id=123456789
        m = re.search(r"modal_id=(\d+)", url)
        if m:
            return m.group(1)
        # Pattern 2: /video/123456789
        m = re.search(r"/video/(\d+)", url)
        if m:
            return m.group(1)
        # Pattern 3: /note/123456789
        m = re.search(r"/note/(\d+)", url)
        if m:
            return m.group(1)
        return None

    def resolve_redirect(self, url: str) -> str:
        """Resolve short links like v.douyin.com/..."""
        if "v.douyin.com" in url or "douyin.com/t/" in url:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": self.user_agent}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.geturl()
            except Exception as e:
                print(f"Error resolving redirect for {url}: {e}")
        return url

    async def parse_single_url(self, raw_url: str) -> Optional[Dict]:
        """Extract metadata and stream URL for a single Douyin video using Playwright."""
        clean_url = raw_url.strip()
        if not clean_url:
            return None

        # Delegate Facebook URLs to FacebookExtractor
        if FacebookExtractor.is_facebook_url(clean_url):
            return await self.fb_extractor.parse_single_url(clean_url)

        # Delegate YouTube URLs to YouTubeExtractor
        if YouTubeExtractor.is_youtube_url(clean_url):
            return await self.yt_extractor.parse_single_url(clean_url)

        # Resolve short URL if needed
        resolved_url = self.resolve_redirect(clean_url)
        video_id = self.extract_video_id(resolved_url)

        # Target URL
        if "modal_id=" in resolved_url:
            target_url = resolved_url
        elif video_id:
            target_url = f"https://www.douyin.com/video/{video_id}"
        else:
            target_url = resolved_url

        captured_details = []
        render_data_info = None

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox"
                ]
            )
            context = await browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1280, "height": 900},
                locale="zh-CN"
            )
            page = await context.new_page()

            async def handle_response(response):
                r_url = response.url
                if "aweme" in r_url and "detail" in r_url:
                    if response.status == 200:
                        try:
                            data = await response.json()
                            if "aweme_detail" in data and data["aweme_detail"]:
                                print(f"[EXTRACTOR] Caught aweme_detail for id={data['aweme_detail'].get('aweme_id')}")
                                captured_details.append(data["aweme_detail"])
                        except Exception as e:
                            pass

            page.on("response", handle_response)

            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
            except Exception as e:
                print(f"[EXTRACTOR] Goto warning: {e}")

            # 1. Immediately inspect page HTML for RENDER_DATA SSR info
            try:
                html = await page.content()
                render_data_info = self._extract_from_render_data(html, raw_url, video_id)
                if render_data_info:
                    print(f"[EXTRACTOR] Successfully parsed RENDER_DATA for id={render_data_info.get('id')}")
            except Exception as e:
                print(f"[EXTRACTOR] Error checking RENDER_DATA: {e}")

            # 2. If RENDER_DATA was not found, wait up to 10 seconds for API detail packet
            if not render_data_info:
                for _ in range(10):
                    if captured_details:
                        break
                    await page.wait_for_timeout(1000)

            await browser.close()

        if render_data_info:
            return render_data_info

        if captured_details:
            return self._format_video_info(captured_details[0], raw_url, video_id)

        return None

    def _extract_from_render_data(self, html: str, raw_url: str, video_id: Optional[str]) -> Optional[Dict]:
        """Extract video metadata and direct play URL from RENDER_DATA script tag."""
        m = re.search(r'id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return None
        try:
            raw = urllib.parse.unquote(m.group(1))
            data = json.loads(raw)
        except Exception as e:
            return None

        def find_video_detail(obj):
            if isinstance(obj, dict):
                if "videoDetail" in obj and isinstance(obj["videoDetail"], dict):
                    return obj["videoDetail"]
                for k, v in obj.items():
                    res = find_video_detail(v)
                    if res:
                        return res
            elif isinstance(obj, list):
                for item in obj:
                    res = find_video_detail(item)
                    if res:
                        return res
            return None

        vd = find_video_detail(data)
        if not vd:
            return None

        aweme_id = str(vd.get("awemeId") or video_id or "unknown")
        desc = vd.get("desc", "").strip() or vd.get("itemTitle", "").strip() or f"Douyin Video {aweme_id}"
        
        author_info = vd.get("authorInfo", {})
        nickname = author_info.get("nickname", "Douyin User")
        author_avatar = ""
        avatar_urls = author_info.get("avatarThumb", {}).get("urlList", [])
        if avatar_urls:
            author_avatar = avatar_urls[0]

        video_info = vd.get("video", {})
        duration_ms = video_info.get("duration", 0)
        duration_sec = round(duration_ms / 1000, 1) if duration_ms else 0
        width = video_info.get("width", 0)
        height = video_info.get("height", 0)
        cover_url = video_info.get("cover", "")

        # Best play url
        best_play_url = ""
        # 1. Check playAddr
        play_addrs = video_info.get("playAddr", [])
        if play_addrs and isinstance(play_addrs, list):
            for pa in play_addrs:
                src = pa.get("src") if isinstance(pa, dict) else pa
                if src and not "media-audio" in src:
                    best_play_url = src
                    break

        # 2. Check bitRateList
        if not best_play_url:
            bit_rates = video_info.get("bitRateList", [])
            if bit_rates:
                sorted_br = sorted(bit_rates, key=lambda x: x.get("bitRate", 0), reverse=True)
                for br in sorted_br:
                    addrs = br.get("playAddr", [])
                    if addrs and isinstance(addrs, list):
                        src = addrs[0].get("src") if isinstance(addrs[0], dict) else addrs[0]
                        if src and not src.endswith("media-video-hvc1/") and not "media-audio" in src:
                            best_play_url = src
                            break

        if not best_play_url:
            return None

        return {
            "id": aweme_id,
            "platform": "douyin",
            "title": desc,
            "author": nickname,
            "author_avatar": author_avatar,
            "cover_url": cover_url,
            "play_url": best_play_url,
            "duration": duration_sec,
            "resolution": f"{width}x{height}" if width and height else "HD",
            "original_url": raw_url,
            "raw_detail": vd
        }


    def _format_video_info(self, detail: Dict, original_url: str, fallback_id: Optional[str]) -> Dict:
        aweme_id = detail.get("aweme_id") or fallback_id or "unknown"
        desc = detail.get("desc", "").strip() or f"Douyin Video {aweme_id}"
        
        author_info = detail.get("author", {})
        nickname = author_info.get("nickname", "Douyin User")
        author_avatar = ""
        if author_info.get("avatar_thumb", {}).get("url_list"):
            author_avatar = author_info["avatar_thumb"]["url_list"][0]

        video_info = detail.get("video", {})
        duration_ms = video_info.get("duration", 0)
        duration_sec = round(duration_ms / 1000, 1) if duration_ms else 0
        width = video_info.get("width", 0)
        height = video_info.get("height", 0)
        
        # Cover image
        cover_url = ""
        covers = video_info.get("cover", {}).get("url_list", []) or video_info.get("origin_cover", {}).get("url_list", [])
        if covers:
            cover_url = covers[0]

        # Highest bitrate play URL (watermark-free)
        best_play_url = ""
        bit_rates = video_info.get("bit_rate", [])
        if bit_rates:
            # Sort by bit_rate descending
            sorted_br = sorted(bit_rates, key=lambda x: x.get("bit_rate", 0), reverse=True)
            for br in sorted_br:
                urls = br.get("play_addr", {}).get("url_list", [])
                if urls:
                    best_play_url = urls[0]
                    break

        if not best_play_url:
            urls = video_info.get("play_addr", {}).get("url_list", [])
            if urls:
                best_play_url = urls[0]

        return {
            "id": aweme_id,
            "platform": "douyin",
            "title": desc,
            "author": nickname,
            "author_avatar": author_avatar,
            "cover_url": cover_url,
            "play_url": best_play_url,
            "duration": duration_sec,
            "resolution": f"{width}x{height}" if width and height else "HD",
            "original_url": original_url,
            "raw_detail": detail
        }

    async def parse_multiple_urls(self, urls: List[str]) -> List[Dict]:
        """Parse multiple Douyin URLs in sequence."""
        results = []
        for u in urls:
            u_clean = u.strip()
            if not u_clean or not ("douyin.com" in u_clean):
                continue
            try:
                info = await self.parse_single_url(u_clean)
                if info:
                    results.append(info)
            except Exception as e:
                print(f"Error parsing URL {u_clean}: {e}")
        return results
