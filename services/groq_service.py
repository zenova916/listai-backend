"""
services/groq_service.py
Generates SEO-optimized eBay listing content using Groq AI.
"""
import os, json, re
from groq import AsyncGroq

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

SYSTEM_PROMPT = """You are a professional eBay US listing specialist with 10 years of experience creating high-converting, SEO-ranked listings.

Return ONLY a valid JSON object. No markdown fences, no thinking, no explanation. Just raw JSON starting with { and ending with }.

Return exactly this structure:
{
  "title": "string — EXACTLY 80 characters max. Format: Brand + Model + Key Feature + Condition + Size/Color. Pack with eBay search keywords.",
  "description": "string — Full HTML-formatted SEO description. Structure it as:\\n\\n<b>Product Overview</b>\\n2-3 sentence intro with keywords.\\n\\n<b>Key Features</b>\\n• Feature 1 with detail\\n• Feature 2 with detail\\n• Feature 3 with detail\\n• Feature 4 with detail\\n• Feature 5 with detail\\n\\n<b>What's Included</b>\\n• List every item in the box\\n\\n<b>Condition Details</b>\\n• Honest condition description\\n\\n<b>Why Buy From Us</b>\\n• Fast shipping • 30-day returns • Secure packaging\\n\\nUse real product knowledge. Be specific. Include model numbers, specs, compatibility.",
  "category": "string — Full eBay US category path e.g. Consumer Electronics > Audio > Headphones",
  "category_id": "string — eBay US numeric category ID",
  "condition": "string — one of: New, Like New, Very Good, Good, Acceptable, For parts",
  "item_specifics": {
    "Brand": "string — exact brand name",
    "Model": "string — exact model name/number",
    "Type": "string — product type",
    "Color": "string — primary color",
    "MPN": "string — manufacturer part number or Unknown",
    "UPC": "string — UPC barcode or Does Not Apply",
    "Country/Region of Manufacture": "string — country",
    "Material": "string — primary material",
    "Style": "string — style descriptor",
    "Size": "string — size if applicable or N/A",
    "Compatible With": "string — compatible devices/systems if applicable",
    "Features": "string — top 3 features comma separated",
    "Power Source": "string — battery/electric/manual etc if applicable",
    "Connectivity": "string — wired/wireless/bluetooth etc if applicable",
    "Warranty": "string — warranty period or No Warranty"
  },
  "price": number,
  "price_low": number,
  "price_high": number
}

RULES:
- Title: Start with Brand, be specific, use real eBay search terms people type, max 80 chars
- Description: Must have bullet points using • character, must have HTML bold tags, minimum 200 words
- Item specifics: Fill ALL fields with real data. Only use N/A if truly not applicable. Never leave blank.
- Price: Realistic current eBay US resale value in USD
- JSON must be valid — no trailing commas, proper escaping of quotes
"""


async def generate_listing_from_title(title: str, condition: str = "Used") -> dict:
    """Single product title → full SEO-optimized eBay listing JSON."""
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Product: {title}\nCondition: {condition}\n\nGenerate the complete eBay listing JSON now. Return ONLY the JSON object, nothing else."}
        ],
        temperature=0.2,
        max_tokens=2000,
    )
    raw = response.choices[0].message.content.strip()
    return _parse_json(raw)


async def generate_listing_from_csv_row(row: dict) -> dict:
    """CSV row dict → full SEO-optimized eBay listing JSON."""
    product_text = "\n".join([f"{k}: {v}" for k, v in row.items() if v and str(v).strip()])
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Product details:\n{product_text}\n\nGenerate the complete eBay listing JSON now. Return ONLY the JSON object, nothing else."}
        ],
        temperature=0.2,
        max_tokens=2000,
    )
    raw = response.choices[0].message.content.strip()
    return _parse_json(raw)


async def generate_from_image(image_bytes: bytes, filename: str, user_id: str, ebay_account_id: str) -> dict:
    """Image upload → product identification → full listing."""
    import base64
    VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
    
    b64 = base64.b64encode(image_bytes).decode()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    media_type = f"image/{ext}" if ext in ("jpg","jpeg","png","webp","gif") else "image/jpeg"
    if ext == "jpg":
        media_type = "image/jpeg"

    response = await client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {
                    "type": "text",
                    "text": "Look at this product image carefully. Identify the brand, model, condition, and all visible features. Then generate a complete eBay US listing JSON. Return ONLY the JSON object, nothing else."
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{b64}"
                    }
                }
            ]}
        ],
        temperature=0.2,
        max_tokens=2000,
    )
    raw = response.choices[0].message.content.strip()
    return _parse_json(raw)

async def generate_demo_listing(title: str) -> dict:
    """Demo version for landing page — no auth required."""
    return await generate_listing_from_title(title, condition="Used")


def _parse_json(raw: str) -> dict:
    """Robustly parse JSON from Groq response, handling all edge cases."""
    if not raw:
        raise ValueError("Empty response from Groq")

    # Remove thinking blocks (some models output <think>...</think>)
    if "<think>" in raw:
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

    # Remove markdown fences
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    # Extract first complete JSON object
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response: {raw[:300]}")

    # Find matching closing brace
    depth = 0
    end = start
    for i, char in enumerate(raw[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    json_str = raw[start:end]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        # Try fixing common issues: trailing commas
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            raise ValueError(f"Could not parse Groq response as JSON: {str(e)} | Raw: {raw[:300]}")
