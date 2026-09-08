import html as html_lib
import json
import re
import urllib.parse
from typing import Dict, Optional
import httpx

class FacebookExtractor:
    """Extractor for public Facebook Reels, Watch, and videos."""

    def __init__(self):
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        self.headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    @staticmethod
    def is_facebook_url(url: str) -> bool:
        """Check if a URL is a Facebook video/reel link."""
        u = url.lower()
        return "facebook.com" in u or "fb.watch" in u or "fb.com" in u

    def extract_video_id(self, url: str) -> str:
        """Extract numeric or alphanumeric ID from Facebook URL."""
        # /reel/1687080395690106
        m = re.search(r"/(?:reel|videos)/(\d+)", url)
        if m:
            return m.group(1)
        # ?v=1687080395690106
        m = re.search(r"[?&]v=(\d+)", url)
        if m:
            return m.group(1)
        # /share/r/ID or /share/v/ID
        m = re.search(r"/share/[rv]/([^/?&]+)", url)
        if m:
            return m.group(1)
        # generic fallback: longest digit sequence
        digits = re.findall(r"\d{8,}", url)
        if digits:
            return digits[0]
        return f"fb_{abs(hash(url)) % 1000000000}"

    async def parse_single_url(self, raw_url: str) -> Optional[Dict]:
        """Fetch and extract Facebook video details."""
        clean_url = raw_url.strip()
        if not clean_url:
            return None

        async with httpx.AsyncClient(headers=self.headers, follow_redirects=True, timeout=25.0) as client:
            try:
                resp = await client.get(clean_url)
                if resp.status_code != 200:
                    print(f"Facebook request failed with status {resp.status_code}")
                    return None
                html_content = resp.text
                final_url = str(resp.url)
            except Exception as e:
                print(f"Error fetching Facebook URL {clean_url}: {e}")
                return None

        video_id = self.extract_video_id(final_url) or self.extract_video_id(clean_url)

        # 1. Extract Stream URLs
        hd_urls = re.findall(r'"browser_native_hd_url":\s*"([^"]+)"', html_content)
        sd_urls = re.findall(r'"browser_native_sd_url":\s*"([^"]+)"', html_content)
        playable_hd = re.findall(r'"playable_url_quality_hd":\s*"([^"]+)"', html_content)
        playable_sd = re.findall(r'"playable_url":\s*"([^"]+)"', html_content)

        play_url = None
        quality = "SD"

        if hd_urls:
            play_url = json.loads(f'"{hd_urls[0]}"')
            quality = "HD 720p"
        elif playable_hd:
            play_url = json.loads(f'"{playable_hd[0]}"')
            quality = "HD 720p"
        elif sd_urls:
            play_url = json.loads(f'"{sd_urls[0]}"')
            quality = "SD"
        elif playable_sd:
            play_url = json.loads(f'"{playable_sd[0]}"')
            quality = "SD"

        if not play_url:
            # Fallback: check og:video
            og_video = re.findall(r'<meta property="og:video" content="([^"]+)"', html_content)
            if og_video:
                play_url = html_lib.unescape(og_video[0])
                quality = "SD"

        if not play_url:
            print(f"Could not extract video stream URL for {clean_url}")
            return None

        # 2. Extract Title & Description
        title = ""
        og_titles = re.findall(r'<meta property="og:title" content="([^"]+)"', html_content)
        if og_titles:
            title = html_lib.unescape(og_titles[0]).strip()
        if not title:
            og_descs = re.findall(r'<meta property="og:description" content="([^"]+)"', html_content)
            if og_descs:
                title = html_lib.unescape(og_descs[0]).strip()
        if not title:
            title = f"Facebook Video {video_id}"

        # Clean title if it contains stats prefix like "197K lượt xem · 3,8K cảm xúc | "
        title_clean = re.sub(r"^[\d.,KMkm\s]+lượt xem[^\w]*[\d.,KMkm\s]*cảm xúc\s*\|\s*", "", title, flags=re.IGNORECASE)
        if title_clean.strip():
            title = title_clean.strip()

        # 3. Extract Author
        author = "Facebook User"
        # Often title is in format: "Caption text | Author Name"
        if "|" in title:
            parts = [p.strip() for p in title.split("|") if p.strip()]
            if len(parts) >= 2:
                # The last part is often the page/author name
                author = parts[-1]
                title = " | ".join(parts[:-1]).strip()

        # 4. Extract Cover Image
        cover_url = ""
        og_images = re.findall(r'<meta property="og:image" content="([^"]+)"', html_content)
        if og_images:
            cover_url = html_lib.unescape(og_images[0])

        # 5. Duration (estimate from JSON or default 0, OpenCV will calculate exact duration)
        duration = 0
        dur_match = re.search(r'"duration_s":\s*(\d+)', html_content)
        if dur_match:
            duration = int(dur_match.group(1))

        return {
            "id": str(video_id),
            "platform": "facebook",
            "title": title,
            "author": author,
            "author_avatar": "",
            "cover_url": cover_url,
            "play_url": play_url,
            "duration": duration,
            "resolution": quality,
            "created_time": "",
            "raw_url": clean_url
        }
