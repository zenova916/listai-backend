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

EBAY_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
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


def _build_add_item_xml(listing: dict, token: str) -> str:
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
    cat   = listing.get("final_category_id") or listing.get("ai_category_id", "99")

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
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <Quantity>1</Quantity>
    <ItemSpecifics>{specifics_xml}</ItemSpecifics>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ShippingDetails>
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
    </ReturnPolicy>
  </Item>
</AddItemRequest>"""


async def publish_to_ebay(listing: dict, access_token_enc: str, sandbox: bool = False) -> dict:
    """Publish a listing to eBay. Returns {item_id, url} or raises."""
    token = decrypt_token(access_token_enc)
    url   = EBAY_SANDBOX_TRADE if sandbox else EBAY_TRADING_URL
    xml   = _build_add_item_xml(listing, token)

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
        msgs   = [e.findtext("e:LongMessage", namespaces=ns) or "" for e in errors]
        raise Exception(f"eBay AddItem failed: {'; '.join(msgs)}")

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
