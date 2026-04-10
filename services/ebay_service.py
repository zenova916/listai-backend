"""
services/ebay_service.py
eBay OAuth token exchange and Trading API (AddItem, GetItem).
eBay US — Site ID 0.
"""
import os, base64, httpx
from xml.etree import ElementTree as ET
from cryptography.fernet import Fernet

EBAY_APP_ID   = os.getenv("EBAY_APP_ID", "")
EBAY_CERT_ID  = os.getenv("EBAY_CERT_ID", "")
EBAY_DEV_ID   = os.getenv("EBAY_DEV_ID", "")
EBAY_REDIRECT = os.getenv("EBAY_REDIRECT_URI", "")
EBAY_SITE_ID  = os.getenv("EBAY_SITE_ID", "0")   # 0 = US
FERNET_KEY    = os.getenv("FERNET_KEY", "")

EBAY_TOKEN_URL     = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_TRADING_URL   = "https://api.ebay.com/ws/api.dll"
EBAY_SANDBOX_TOKEN = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
EBAY_SANDBOX_TRADE = "https://api.sandbox.ebay.com/ws/api.dll"

EBAY_SCOPES = "%20".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
])


# ── Encryption helpers ────────────────────────────────────────

def _fernet():
    if not FERNET_KEY:
        raise RuntimeError("FERNET_KEY not set in .env")
    return Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)

def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()

def decrypt_token(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


# ── OAuth flow ────────────────────────────────────────────────

def get_auth_url(sandbox: bool = False) -> str:
    base = "https://auth.sandbox.ebay.com" if sandbox else "https://auth.ebay.com"
    return (
        f"{base}/oauth2/authorize"
        f"?client_id={EBAY_APP_ID}"
        f"&redirect_uri={EBAY_REDIRECT}"
        f"&response_type=code"
        f"&scope={EBAY_SCOPES}"
        f"&prompt=login"
    )


async def exchange_code(code: str, sandbox: bool = False) -> dict:
    """Exchange auth code for access + refresh tokens."""
    url = EBAY_SANDBOX_TOKEN if sandbox else EBAY_TOKEN_URL
    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": EBAY_REDIRECT,
            },
            timeout=15,
        )
    return r.json()


# ── Trading API ───────────────────────────────────────────────

async def get_seller_policies(access_token: str, sandbox: bool = False) -> dict:
    """Fetch seller's business policy IDs from eBay Account API."""
    base = "https://api.sandbox.ebay.com" if sandbox else "https://api.ebay.com"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    policies = {"shipping_id": None, "return_id": None, "payment_id": None}
    async with httpx.AsyncClient(timeout=15) as client:
        # Fulfillment (shipping) policies
        try:
            r = await client.get(f"{base}/sell/account/v1/fulfillment_policy?marketplace_id=EBAY_US", headers=headers)
            if r.status_code == 200:
                data = r.json()
                items = data.get("fulfillmentPolicies", [])
                if items:
                    policies["shipping_id"] = str(items[0]["fulfillmentPolicyId"])
        except Exception as e:
            print(f"[eBay] Could not fetch fulfillment policy: {e}")

        # Return policies
        try:
            r = await client.get(f"{base}/sell/account/v1/return_policy?marketplace_id=EBAY_US", headers=headers)
            if r.status_code == 200:
                data = r.json()
                items = data.get("returnPolicies", [])
                if items:
                    policies["return_id"] = str(items[0]["returnPolicyId"])
        except Exception as e:
            print(f"[eBay] Could not fetch return policy: {e}")

        # Payment policies
        try:
            r = await client.get(f"{base}/sell/account/v1/payment_policy?marketplace_id=EBAY_US", headers=headers)
            if r.status_code == 200:
                data = r.json()
                items = data.get("paymentPolicies", [])
                if items:
                    policies["payment_id"] = str(items[0]["paymentPolicyId"])
        except Exception as e:
            print(f"[eBay] Could not fetch payment policy: {e}")

    print(f"[eBay] Policies fetched: {policies}")
    return policies


# ── eBay US Category ID map ──────────────────────────────────
EBAY_CATEGORY_MAP = {
    # Electronics
    "cell phones & smartphones": "9355",
    "apple iphones": "9355",
    "samsung phones": "9355",
    "laptops & netbooks": "177",
    "apple laptops": "111422",
    "tablets & ebook readers": "171485",
    "headphones": "112529",
    "portable audio & headphones": "15052",
    "cameras & photo": "625",
    "digital cameras": "31388",
    "video games": "1249",
    "video game consoles": "139971",
    "televisions": "11071",
    "tv, video & home audio": "11071",
    "computer components & parts": "175673",
    "printers & scanners": "1245",
    "consumer electronics": "293",
    # Clothing
    "men's clothing": "1059",
    "women's clothing": "15724",
    "men's shoes": "93427",
    "women's shoes": "3034",
    "men's t-shirts": "185100",
    "women's dresses": "63861",
    "hoodies & sweatshirts": "185101",
    "jeans": "11483",
    "men's accessories": "4250",
    "women's accessories": "4251",
    # Jewelry & Watches
    "fine jewelry": "281",
    "fashion jewelry": "10968",
    "rings": "67726",
    "necklaces & pendants": "164342",
    "bracelets": "137835",
    "earrings": "10985",
    "watches": "31387",
    "wristwatches": "31387",
    # Home & Garden
    "home & garden": "11700",
    "furniture": "3197",
    "kitchen & dining": "20625",
    "kitchen, dining & bar": "20625",
    "bedding": "20444",
    "tools & workshop equipment": "631",
    "garden & patio": "159912",
    "home décor": "10033",
    "lamps, lighting & ceiling fans": "20697",
    # Health & Beauty
    "health & beauty": "26395",
    "hair care": "11854",
    "hair care & styling": "11854",
    "hair dryers": "20710",
    "hair dryers & styling tools": "20710",
    "hair dryers & hair care appliances": "20710",
    "skin care": "11863",
    "makeup": "31786",
    "fragrances": "180345",
    "vitamins & dietary supplements": "180959",
    # Toys & Hobbies
    "toys & hobbies": "220",
    "lego sets": "19006",
    "action figures": "246",
    "dolls & bears": "238",
    "building toys": "183446",
    "die-cast & toy vehicles": "222",
    "model trains": "180250",
    # Sporting Goods
    "sporting goods": "888",
    "golf": "1513",
    "cycling": "7294",
    "exercise & fitness": "15273",
    "fishing": "724",
    "outdoor sports": "1000",
    # Books & Media
    "books": "267",
    "music cds": "306",
    "movies & tv": "11232",
    "video games & consoles": "1249",
    # Collectibles
    "collectibles": "1",
    "coins": "11116",
    "sports cards": "212",
    "stamps": "260",
    "art": "550",
    # Automotive
    "auto parts & accessories": "6028",
    "car electronics": "3270",
    "motorcycles": "6024",
    # Baby
    "baby": "2984",
    "strollers": "66696",
    # Musical Instruments
    "musical instruments": "619",
    "guitars": "33034",
    "pianos & keyboards": "16218",
    # Pet Supplies
    "pet supplies": "1281",
    "dog supplies": "20744",
    "cat supplies": "20741",
    # Business & Industrial
    "business & industrial": "12576",
    "office equipment": "2536",
}

def _lookup_category_id(category_path: str) -> str:
    """Look up eBay category ID from category name/path."""
    if not category_path:
        return ""
    path_lower = category_path.lower().strip()
    # Try full path first
    if path_lower in EBAY_CATEGORY_MAP:
        return EBAY_CATEGORY_MAP[path_lower]
    # Try each segment from most specific (last) to least specific (first)
    parts = [p.strip().lower() for p in path_lower.split(">")]
    for part in reversed(parts):
        if part in EBAY_CATEGORY_MAP:
            return EBAY_CATEGORY_MAP[part]
        # Try partial match
        for key, val in EBAY_CATEGORY_MAP.items():
            if part in key or key in part:
                return val
    return ""


def _condition_id(condition: str) -> str:
    mapping = {
        "New": "1000", "Like New": "1500", "Very Good": "2000",
        "Good": "3000", "Acceptable": "4000", "For parts": "7000",
    }
    return mapping.get(condition, "3000")


def _esc(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _build_add_item_xml(listing: dict, token: str, policies: dict = None) -> str:
    import json
    specifics = {}
    try:
        raw = listing.get("final_specifics") or listing.get("ai_specifics") or "{}"
        if isinstance(raw, str):
            specifics = json.loads(raw)
        elif isinstance(raw, dict):
            specifics = raw
    except Exception:
        pass

    specifics_xml = "".join([
        f"<NameValueList><Name>{_esc(k)}</Name><Value>{_esc(v)}</Value></NameValueList>"
        for k, v in specifics.items() if v
    ])

    title = _esc((listing.get("final_title") or listing.get("ai_title", ""))[:80])
    desc  = listing.get("final_description") or listing.get("ai_description", "")
    price = listing.get("final_price") or listing.get("ai_price", 9.99)
    cond  = listing.get("final_condition") or listing.get("ai_condition", "Used")
    cat = listing.get("final_category_id") or listing.get("ai_category_id", "")
    if not cat or cat in ("99", "0", "None", "", "null"):
        cat_name = listing.get("ai_category", "") or ""
        cat = _lookup_category_id(cat_name)
    if not cat:
        raise Exception("Could not determine eBay category. Please select a category from the dropdown before publishing.")
    print(f"[eBay] Using category ID: {cat}")

    policies = policies or {}
    shipping_id = policies.get("shipping_id")
    return_id   = policies.get("return_id")
    payment_id  = policies.get("payment_id")

    if shipping_id or return_id or payment_id:
        # eBay business policies — correct XML structure
        seller_profiles = "<SellerProfiles>"
        if payment_id:
            seller_profiles += (
                f"<SellerPaymentProfile>"
                f"<PaymentProfileID>{payment_id}</PaymentProfileID>"
                f"<PaymentProfileName>placeholder</PaymentProfileName>"
                f"</SellerPaymentProfile>"
            )
        if shipping_id:
            seller_profiles += (
                f"<SellerShippingProfile>"
                f"<ShippingProfileID>{shipping_id}</ShippingProfileID>"
                f"<ShippingProfileName>placeholder</ShippingProfileName>"
                f"</SellerShippingProfile>"
            )
        if return_id:
            seller_profiles += (
                f"<SellerReturnProfile>"
                f"<ReturnProfileID>{return_id}</ReturnProfileID>"
                f"<ReturnProfileName>placeholder</ReturnProfileName>"
                f"</SellerReturnProfile>"
            )
        seller_profiles += "</SellerProfiles>"
        shipping_block = seller_profiles
    else:
        # Fallback: legacy fields for accounts without business policies
        shipping_block = """<ShippingDetails>
      <ShippingServiceOptions>
        <ShippingService>USPSPriority</ShippingService>
        <ShippingServiceCost currencyID="USD">0.00</ShippingServiceCost>
        <ShippingServicePriority>1</ShippingServicePriority>
        <FreeShipping>true</FreeShipping>
      </ShippingServiceOptions>
    </ShippingDetails>
    <ReturnPolicy>
      <ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>
      <RefundOption>MoneyBack</RefundOption>
      <ReturnsWithinOption>Days_30</ReturnsWithinOption>
      <ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>
    </ReturnPolicy>"""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
  <Item>
    <Title>{title}</Title>
    <Description><![CDATA[{desc}]]></Description>
    <PrimaryCategory><CategoryID>{cat}</CategoryID></PrimaryCategory>
    <StartPrice currencyID="USD">{price:.2f}</StartPrice>
    <ConditionID>{_condition_id(cond)}</ConditionID>
    <Country>US</Country>
    <Currency>USD</Currency>
    <Location>United States</Location>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <Quantity>1</Quantity>
    <ItemSpecifics>{specifics_xml}</ItemSpecifics>
    <DispatchTimeMax>3</DispatchTimeMax>
    {shipping_block}
  </Item>
</AddItemRequest>"""


async def publish_to_ebay(listing: dict, access_token_enc: str, sandbox: bool = False, refresh_token_enc: str = None, account_id: str = None) -> dict:
    """Publish a listing to eBay. Returns {item_id, url} or raises."""
    token = decrypt_token(access_token_enc)
    url   = EBAY_SANDBOX_TRADE if sandbox else EBAY_TRADING_URL
    # Fetch seller's business policies automatically
    policies = await get_seller_policies(token, sandbox=sandbox)
    xml   = _build_add_item_xml(listing, token, policies=policies)

    headers = {
        "X-EBAY-API-SITEID":              EBAY_SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME":           "AddItem",
        "X-EBAY-API-APP-NAME":            EBAY_APP_ID,
        "X-EBAY-API-DEV-NAME":            EBAY_DEV_ID,
        "X-EBAY-API-CERT-NAME":           EBAY_CERT_ID,
        "Content-Type":                   "text/xml",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, content=xml.encode("utf-8"), headers=headers)

    ns   = {"e": "urn:ebay:apis:eBLBaseComponents"}
    root = ET.fromstring(r.text)
    ack  = root.findtext("e:Ack", namespaces=ns)

    if ack not in ("Success", "Warning"):
        errors = root.findall(".//e:Error", namespaces=ns)
        msgs   = [e.findtext("e:LongMessage", namespaces=ns) or e.findtext("e:ShortMessage", namespaces=ns) or "" for e in errors]
        error_text = "; ".join(m for m in msgs if m) or r.text[:500]
        print(f"[eBay] Publish failed. Ack={ack}. Errors: {error_text}")
        raise Exception(f"eBay AddItem failed: {error_text}")

    item_id = root.findtext("e:ItemID", namespaces=ns)
    base    = "sandbox.ebay.com" if sandbox else "ebay.com"
    return {"item_id": item_id, "url": f"https://www.{base}/itm/{item_id}"}


async def get_item_status(item_id: str, access_token_enc: str, sandbox: bool = False) -> str:
    """Get current listing status from eBay."""
    token = decrypt_token(access_token_enc)
    url   = EBAY_SANDBOX_TRADE if sandbox else EBAY_TRADING_URL
    xml   = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>
  <ItemID>{item_id}</ItemID>
</GetItemRequest>"""

    headers = {
        "X-EBAY-API-SITEID":              EBAY_SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME":           "GetItem",
        "X-EBAY-API-APP-NAME":            EBAY_APP_ID,
        "Content-Type":                   "text/xml",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, content=xml.encode(), headers=headers)

    ns   = {"e": "urn:ebay:apis:eBLBaseComponents"}
    root = ET.fromstring(r.text)
    return root.findtext(".//e:ListingStatus", namespaces=ns) or "Unknown"
