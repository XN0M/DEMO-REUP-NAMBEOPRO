import asyncio
import json
import re
from typing import Dict, List, Optional
import httpx

class DouyinTranslator:
    def __init__(self):
        self.default_gemini_model = "gemini-3-flash-preview"
        self.gemini_api_url = "https://generativelanguage.googleapis.com/v1beta/models"
        self.default_openai_model = "gpt-4o-mini"
        self.default_openai_base_url = "https://api.openai.com/v1"

    async def _call_gemini(self, prompt: str, api_key: str, model: str = "gemini-3-flash-preview") -> str:
        """Call Gemini API directly with httpx with automatic fallback to gemini-flash-latest on 404."""
        target_model = (model or self.default_gemini_model).strip()
        if target_model.startswith("models/"):
            target_model = target_model[len("models/"):]

        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "topP": 0.95
            }
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            url = f"{self.gemini_api_url}/{target_model}:generateContent?key={api_key}"
            resp = await client.post(url, headers=headers, json=payload)

            # Smart fallback candidates if model returns 404 (retired/invalid), 429 (quota/rate-limit), or 503 (high demand)
            if resp.status_code in [404, 429, 503]:
                fallback_candidates = [
                    "gemini-3-flash-preview",
                    "gemini-3.1-flash-lite",
                    "gemini-flash-lite-latest",
                    "gemini-2.5-flash",
                    "gemini-3.8-flash"
                ]
                # Filter out the model that already failed
                tried_models = {target_model}

                for fb in fallback_candidates:
                    if fb in tried_models:
                        continue
                    tried_models.add(fb)
                    fb_url = f"{self.gemini_api_url}/{fb}:generateContent?key={api_key}"
                    try:
                        fb_resp = await client.post(fb_url, headers=headers, json=payload)
                        if fb_resp.status_code == 200:
                            resp = fb_resp
                            target_model = fb
                            break
                    except Exception:
                        pass

                # If still 429 or 503 after trying alternative models, wait 2.5s and retry primary model once
                if resp.status_code in [429, 503]:
                    await asyncio.sleep(2.5)
                    retry_url = f"{self.gemini_api_url}/{target_model}:generateContent?key={api_key}"
                    try:
                        retry_resp = await client.post(retry_url, headers=headers, json=payload)
                        if retry_resp.status_code == 200:
                            resp = retry_resp
                    except Exception:
                        pass

            if resp.status_code != 200:
                err_detail = resp.text
                try:
                    err_json = resp.json()
                    err_detail = err_json.get("error", {}).get("message", resp.text)
                except Exception:
                    pass
                raise RuntimeError(f"Gemini API Error ({resp.status_code}, model '{target_model}'): {err_detail}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError(f"Gemini API ({target_model}) không trả về nội dung hợp lệ.")
            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return text

    async def _call_openai(
        self,
        prompt: str,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1"
    ) -> str:
        """Call OpenAI/ChatGPT Chat Completions API with httpx, supporting custom base URL."""
        clean_base = (base_url or self.default_openai_base_url).strip().rstrip("/")
        if not clean_base.endswith("/chat/completions"):
            endpoint = f"{clean_base}/chat/completions"
        else:
            endpoint = clean_base

        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model or self.default_openai_model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            if resp.status_code != 200:
                err_detail = resp.text
                try:
                    err_json = resp.json()
                    err_detail = err_json.get("error", {}).get("message", resp.text)
                except Exception:
                    pass
                raise RuntimeError(f"OpenAI API Error ({resp.status_code}): {err_detail}")

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("OpenAI API không trả về kết quả hợp lệ.")
            text = choices[0].get("message", {}).get("content", "")
            return text

    async def _call_ai(
        self,
        prompt: str,
        api_key: str,
        provider: str = "gemini",
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ) -> str:
        """Route call to chosen provider (gemini or openai)."""
        prov = (provider or "gemini").strip().lower()
        if prov in ["openai", "chatgpt"]:
            return await self._call_openai(
                prompt=prompt,
                api_key=api_key,
                model=model or self.default_openai_model,
                base_url=base_url or self.default_openai_base_url
            )
        else:
            return await self._call_gemini(
                prompt=prompt,
                api_key=api_key,
                model=model or self.default_gemini_model
            )

    async def test_connection(
        self,
        api_key: str,
        provider: str = "gemini",
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ) -> Dict:
        """Quickly test connection and model validity with a 1-sentence prompt."""
        import time
        t0 = time.time()
        prov = (provider or "gemini").strip().lower()
        test_prompt = "Hãy trả lời đúng 1 từ duy nhất: 'OK'"
        resp = await self._call_ai(
            prompt=test_prompt,
            api_key=api_key,
            provider=prov,
            model=model,
            base_url=base_url
        )
        elapsed = round(time.time() - t0, 2)
        return {
            "status": "success",
            "provider": prov,
            "model": model or (self.default_gemini_model if prov == "gemini" else self.default_openai_model),
            "latency_seconds": elapsed,
            "response": resp.strip()[:100]
        }

    def _salvage_translation_items(self, text: str) -> List[Dict]:
        """Salvage incomplete or truncated JSON array of translations using regex."""
        pattern = re.compile(r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"vietnamese"\s*:\s*"((?:\\.|[^"\\])*)"', re.DOTALL)
        results = []
        for match in pattern.finditer(text):
            try:
                item_id = int(match.group(1))
                vi_raw = match.group(2)
                vi_clean = json.loads(f'"{vi_raw}"')
                results.append({"id": item_id, "vietnamese": vi_clean})
            except Exception:
                pass
        return results

    def _extract_json_block(self, text: str) -> Optional[Dict | List]:
        """Clean markdown code fences and parse JSON safely."""
        text = text.strip()
        # Remove ```json ... ```
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()
        try:
            return json.loads(text)
        except Exception:
            # Try finding first [ or { to last ] or }
            start_bracket = min([i for i in [text.find('{'), text.find('[')] if i != -1] or [-1])
            end_bracket = max([text.rfind('}'), text.rfind(']')] or [-1])
            if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
                try:
                    return json.loads(text[start_bracket:end_bracket + 1])
                except Exception:
                    pass

        # Fallback salvage if json array was truncated
        salvaged = self._salvage_translation_items(text)
        if salvaged:
            return salvaged
        return None

    async def generate_character_map(
        self,
        segments: List[Dict],
        api_key: str,
        video_title: str = "",
        provider: str = "gemini",
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ) -> Dict:
        """Analyze dialogue to extract character roles, context, and consistent pronoun rules."""
        provider_name = "ChatGPT/OpenAI" if (provider or "").lower() in ["openai", "chatgpt"] else "Google Gemini"
        if not api_key or not api_key.strip():
            raise ValueError(f"Vui lòng cấu hình API Key cho {provider_name} trong phần Cài đặt AI.")

        # Prepare dialogue lines summary
        dialogue_lines = []
        for s in segments[:80]: # Send up to 80 lines for role analysis
            dialogue_lines.append(f"[{s['start']}s] {s.get('chinese') or s.get('text', '')}")
        dialogue_text = "\n".join(dialogue_lines)

        prompt = f"""Bạn là một chuyên gia biên kịch và dịch thuật tiếng Trung - tiếng Việt kỳ cựu.
Dưới đây là lời thoại trích xuất từ một video Douyin:
Tiêu đề video: {video_title}
Kịch bản thoại:
{dialogue_text}

Hãy phân tích kỹ để LẬP BẢNG PHÂN VAI VÀ QUY TẮC XƯNG HÔ NHẤT QUÁN:
1. Nhận diện thể loại và bối cảnh video (ví dụ: anime cổ trang, kiếm hiệp, đời thường, vlog phòng gym, chuyện gia đình...).
2. Liệt kê các nhân vật và vai vế của họ.
3. Xác định quy tắc xưng hô cụ thể giữa từng cặp nhân vật (Ai xưng gì với ai? Ví dụ: Cô nãi nãi xưng 'ta/bà cô' - gọi 'các cháu/ngươi', các cháu xưng 'cháu/con' - gọi 'cô nãi nãi/lão tổ tông').
Tránh tuyệt đối việc xưng 'tôi - bạn' xa lạ nếu đây là quan hệ thân mật, gia đình, anime hoặc cổ trang!

BẮT BUỘC trả về định dạng JSON thuần túy (không kèm giải thích bên ngoài) theo mẫu:
{{
  "context_summary": "Tóm tắt bối cảnh và thể loại video",
  "characters": [
    {{
      "name": "Tên hoặc vai vế nhân vật",
      "role": "Mô tả vai trò / tuổi tác / địa vị",
      "self_pronoun": "Cách tự xưng (ví dụ: ta, cô, anh, em, cháu, mình...)",
      "calls_others": "Cách gọi người đối thoại (ví dụ: các cháu, ngươi, em, anh, bạn...)"
    }}
  ],
  "relationship_notes": "Ghi chú quy tắc xưng hô then chốt để dịch đồng bộ từ đầu đến cuối"
}}"""

        raw_resp = await self._call_ai(
            prompt=prompt,
            api_key=api_key.strip(),
            provider=provider,
            model=model,
            base_url=base_url
        )
        parsed = self._extract_json_block(raw_resp)
        if isinstance(parsed, dict) and "characters" in parsed:
            return parsed

        return {
            "context_summary": "Video Douyin thông thường",
            "characters": [
                {
                    "name": "Nhân vật chính",
                    "role": "Người nói trong video",
                    "self_pronoun": "mình / tôi / anh",
                    "calls_others": "các bạn / em"
                }
            ],
            "relationship_notes": "Xưng hô tự nhiên theo ngữ cảnh video."
        }

    async def translate_chunk(
        self,
        chunk_segments: List[Dict],
        character_map: Dict,
        api_key: str,
        provider: str = "gemini",
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ) -> List[Dict]:
        """Translate a single chunk of segments (e.g. 10-20 items) strictly following Character Map."""
        provider_name = "ChatGPT/OpenAI" if (provider or "").lower() in ["openai", "chatgpt"] else "Google Gemini"
        if not api_key or not api_key.strip():
            raise ValueError(f"Vui lòng cấu hình API Key cho {provider_name} trong phần Cài đặt AI.")

        if not chunk_segments:
            return []

        char_map_str = json.dumps(character_map or {}, ensure_ascii=False, indent=2)
        items_for_prompt = [{"id": s["id"], "text": s.get("chinese") or s.get("text", "")} for s in chunk_segments]
        items_str = json.dumps(items_for_prompt, ensure_ascii=False, indent=2)

        prompt = f"""Bạn là biên kịch và dịch giả tiếng Trung -> tiếng Việt hàng đầu cho video ngắn (Douyin / TikTok / Reels).
Dưới đây là BẢNG PHÂN VAI & QUY TẮC XƯNG HÔ ĐÃ DUYỆT:
{char_map_str}

DANH SÁCH CÂU THOẠI CẦN DỊCH:
{items_str}

HÃY DỊCH TỪNG CÂU SANG TIẾNG VIỆT TỰ NHIÊN, CHUẨN XÁC THEO CÁC NGUYÊN TẮC:
1. Tuân thủ 100% ngôi xưng hô theo BẢNG PHÂN VAI trên xuyên suốt từng câu, tuyệt đối không xưng 'tôi - bạn' cứng nhắc.
2. Phong cách video ngắn hiện đại: Câu thoại gãy gọn, súc tích, tự nhiên, nhịp điệu nhanh, dễ nghe khi lồng tiếng.
3. Tự động loại bỏ các từ đệm, thán từ thừa hoặc ậm ừ tiếng Trung (như '啊', '呃', '就是说', '那个', '嗯').
4. Giữ trọn cảm xúc, ngữ cảnh và thần thái của nhân vật.
5. BẮT BUỘC trả về JSON mảng đúng định dạng:
[
  {{"id": 1, "vietnamese": "Nội dung dịch tiếng Việt"}},
  ...
]"""

        raw_resp = await self._call_ai(
            prompt=prompt,
            api_key=api_key.strip(),
            provider=provider,
            model=model,
            base_url=base_url
        )
        parsed_list = self._extract_json_block(raw_resp)

        translated = [dict(s) for s in chunk_segments]
        if isinstance(parsed_list, list):
            lookup = {item["id"]: item.get("vietnamese", "") for item in parsed_list if isinstance(item, dict) and "id" in item}
            for s in translated:
                s_id = s["id"]
                if s_id in lookup and lookup[s_id]:
                    s["vietnamese"] = lookup[s_id].strip()

        return translated

    async def translate_with_character_map(
        self,
        segments: List[Dict],
        character_map: Dict,
        api_key: str,
        provider: str = "gemini",
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        chunk_size: int = 20
    ) -> List[Dict]:
        """Translate all segments into natural Vietnamese concurrently in chunks strictly following the Character Map."""
        if not segments:
            return []

        chunks = [segments[i:i + chunk_size] for i in range(0, len(segments), chunk_size)]
        sem = asyncio.Semaphore(4)

        async def run_chunk(c):
            async with sem:
                return await self.translate_chunk(
                    chunk_segments=c,
                    character_map=character_map,
                    api_key=api_key,
                    provider=provider,
                    model=model,
                    base_url=base_url
                )

        results = await asyncio.gather(*(run_chunk(c) for c in chunks))
        all_translated = []
        for r in results:
            all_translated.extend(r)

        return all_translated
