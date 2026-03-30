"""
services/groq_service.py
Generates eBay listing content using Groq (free, Llama 3.1 70B).
"""
import os, json
from groq import AsyncGroq

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are an expert eBay US listing copywriter with 10 years of experience.
Your listings rank highly in eBay search and convert browsers into buyers.

Given a product, return ONLY a valid JSON object — no markdown fences, no explanation, just JSON.

Return exactly this structure:
{
  "title": "string — max 80 characters, keyword-rich, starts with Brand + Model",
  "description": "string — 150-300 words, plain text, highlight condition/features/what's included",
  "category": "string — eBay US category path e.g. Consumer Electronics > Audio > Portable Players",
  "category_id": "string — eBay US numeric category ID best guess",
  "condition": "string — one of: New, Like New, Very Good, Good, Acceptable, For parts",
  "item_specifics": {
    "Brand": "string",
    "Model": "string",
    "Type": "string",
    "Color": "string",
    "MPN": "string or Unknown"
  },
  "price": number,
  "price_low": number,
  "price_high": number
}

Title rules: Brand + Model + key feature + condition word + max 80 chars total.
Price rules: Realistic USD resale price for eBay US. price = midpoint of low/high.
Item specifics: Include 4-8 relevant fields. Use eBay US naming conventions.
"""


async def generate_listing_from_title(title: str, condition: str = "Used") -> dict:
    """Single product title → full eBay listing JSON."""
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Product: {title}\nCondition: {condition}\n\nGenerate the eBay listing JSON."}
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    raw = response.choices[0].message.content.strip()
    return _parse_json(raw)


async def generate_listing_from_csv_row(row: dict) -> dict:
    """CSV row dict → full eBay listing JSON."""
    product_text = "\n".join([f"{k}: {v}" for k, v in row.items() if v and str(v).strip()])
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Product details from spreadsheet:\n{product_text}\n\nGenerate the eBay listing JSON."}
        ],
        temperature=0.2,
        max_tokens=1024,
    )
    raw = response.choices[0].message.content.strip()
    return _parse_json(raw)


async def generate_demo_listing(title: str) -> dict:
    """
    Demo version — same as generate_listing_from_title.
    Called from the landing page (no auth required).
    """
    return await generate_listing_from_title(title, condition="Used")


def _parse_json(raw: str) -> dict:
    """Safely parse JSON from Groq response, stripping any markdown fences."""
    # Strip ```json ... ``` if model wraps it
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Find first { to last } and try again
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
        raise ValueError(f"Could not parse Groq response as JSON: {raw[:200]}")
