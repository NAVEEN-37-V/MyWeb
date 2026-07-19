"from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / \".env\")

import os
import uuid
import logging
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

# ---------- Config ----------
JWT_ALGORITHM = \"HS256\"
JWT_SECRET = os.environ[\"JWT_SECRET\"]
ADMIN_EMAIL = os.environ[\"ADMIN_EMAIL\"]
ADMIN_PASSWORD = os.environ[\"ADMIN_PASSWORD\"]
AFFILIATE_TAG = os.environ.get(\"AMAZON_AFFILIATE_TAG\", \"4StarAbove\")

# ---------- DB ----------
mongo_url = os.environ[\"MONGO_URL\"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ[\"DB_NAME\"]]

# ---------- App ----------
app = FastAPI(title=\"4StarAbove Amazon Affiliate API\")
api_router = APIRouter(prefix=\"/api\")

logging.basicConfig(level=logging.INFO, format=\"%(asctime)s - %(name)s - %(levelname)s - %(message)s\")
logger = logging.getLogger(\"4starabove\")

# ---------- Categories (fixed list) ----------
CATEGORIES = [
    {\"slug\": \"electronics\", \"name\": \"Electronics\", \"tagline\": \"Gadgets, audio, wearables & more\",
     \"image\": \"https://images.pexels.com/photos/14309807/pexels-photo-14309807.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940\"},
    {\"slug\": \"home\", \"name\": \"Home\", \"tagline\": \"Elevate your everyday space\",
     \"image\": \"https://images.pexels.com/photos/28795082/pexels-photo-28795082.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940\"},
    {\"slug\": \"fashion\", \"name\": \"Fashion\", \"tagline\": \"Wearable statements & essentials\",
     \"image\": \"https://images.unsplash.com/photo-1544411047-c491e34a24e0?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjAzOTB8MHwxfHNlYXJjaHwxfHxlbGVjdHJvbmljcyUyMGhvbWUlMjBmYXNoaW9uJTIwZGFyayUyMGJhY2tncm91bmR8ZW58MHx8fHwxNzg0NDg0MzY3fDA&ixlib=rb-4.1.0&q=85\"},
    {\"slug\": \"books\", \"name\": \"Books\", \"tagline\": \"Stories, knowledge & inspiration\",
     \"image\": \"https://images.unsplash.com/photo-1763510385683-6374fac8df54?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1Mjh8MHwxfHNlYXJjaHwyfHxiZWF1dHklMjBzcG9ydHMlMjBib29rcyUyMGRhcmslMjBhZXN0aGV0aWN8ZW58MHx8fHwxNzg0NDg0MzY3fDA&ixlib=rb-4.1.0&q=85\"},
    {\"slug\": \"beauty\", \"name\": \"Beauty\", \"tagline\": \"Skincare, tools & self-care\",
     \"image\": \"https://images.unsplash.com/photo-1730391002761-f9a7175e0710?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1Mjh8MHwxfHNlYXJjaHwzfHxiZWF1dHklMjBzcG9ydHMlMjBib29rcyUyMGRhcmslMjBhZXN0aGV0aWN8ZW58MHx8fHwxNzg0NDg0MzY3fDA&ixlib=rb-4.1.0&q=85\"},
    {\"slug\": \"sports\", \"name\": \"Sports\", \"tagline\": \"Gear for movement & recovery\",
     \"image\": \"https://images.unsplash.com/photo-1714564209284-1ba07d735e76?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1Mjh8MHwxfHNlYXJjaHwxfHxiZWF1dHklMjBzcG9ydHMlMjBib29rcyUyMGRhcmslMjBhZXN0aGV0aWN8ZW58MHx8fHwxNzg0NDg0MzY3fDA&ixlib=rb-4.1.0&q=85\"},
]
CATEGORY_SLUGS = {c[\"slug\"] for c in CATEGORIES}


# ---------- Auth helpers ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(\"utf-8\"), bcrypt.gensalt()).decode(\"utf-8\")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(\"utf-8\"), hashed.encode(\"utf-8\"))


def create_access_token(user_id: str, email: str) -> str:
    payload = {\"sub\": user_id, \"email\": email, \"type\": \"access\",
               \"exp\": datetime.now(timezone.utc) + timedelta(hours=8)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {\"sub\": user_id, \"type\": \"refresh\",
               \"exp\": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie(\"access_token\", access, httponly=True, secure=False,
                        samesite=\"lax\", max_age=8 * 60 * 60, path=\"/\")
    response.set_cookie(\"refresh_token\", refresh, httponly=True, secure=False,
                        samesite=\"lax\", max_age=7 * 24 * 60 * 60, path=\"/\")


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get(\"access_token\")
    if not token:
        auth_header = request.headers.get(\"Authorization\", \"\")
        if auth_header.startswith(\"Bearer \"):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail=\"Not authenticated\")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get(\"type\") != \"access\":
            raise HTTPException(status_code=401, detail=\"Invalid token type\")
        user = await db.users.find_one({\"id\": payload[\"sub\"]}, {\"_id\": 0, \"password_hash\": 0})
        if not user:
            raise HTTPException(status_code=401, detail=\"User not found\")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail=\"Token expired\")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail=\"Invalid token\")


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get(\"role\") != \"admin\":
        raise HTTPException(status_code=403, detail=\"Admin access required\")
    return user


# ---------- Models ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProductBase(BaseModel):
    title: str
    description: str = \"\"
    price: str = \"\"
    image_url: str
    amazon_url: str
    category: str
    rating: float = 4.5
    featured: bool = False


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[str] = None
    image_url: Optional[str] = None
    amazon_url: Optional[str] = None
    category: Optional[str] = None
    rating: Optional[float] = None
    featured: Optional[bool] = None


class Product(ProductBase):
    id: str
    created_at: str


def with_affiliate_tag(url: str) -> str:
    if not url:
        return url
    sep = \"&\" if \"?\" in url else \"?\"
    if f\"tag={AFFILIATE_TAG}\" in url:
        return url
    return f\"{url}{sep}tag={AFFILIATE_TAG}\"


# ---------- Routes: Auth ----------
@api_router.post(\"/auth/login\")
async def login(payload: LoginRequest, response: Response):
    email = payload.email.lower()
    user = await db.users.find_one({\"email\": email})
    if not user or not verify_password(payload.password, user[\"password_hash\"]):
        raise HTTPException(status_code=401, detail=\"Invalid email or password\")
    access = create_access_token(user[\"id\"], email)
    refresh = create_refresh_token(user[\"id\"])
    set_auth_cookies(response, access, refresh)
    return {\"id\": user[\"id\"], \"email\": user[\"email\"], \"name\": user.get(\"name\", \"\"), \"role\": user[\"role\"]}


@api_router.post(\"/auth/logout\")
async def logout(response: Response, _: dict = Depends(get_current_user)):
    response.delete_cookie(\"access_token\", path=\"/\")
    response.delete_cookie(\"refresh_token\", path=\"/\")
    return {\"ok\": True}


@api_router.get(\"/auth/me\")
async def me(user: dict = Depends(get_current_user)):
    return user


# ---------- Routes: Categories ----------
@api_router.get(\"/categories\")
async def list_categories():
    results = []
    for c in CATEGORIES:
        count = await db.products.count_documents({\"category\": c[\"slug\"]})
        results.append({**c, \"product_count\": count})
    return results


@api_router.get(\"/categories/{slug}\")
async def get_category(slug: str):
    for c in CATEGORIES:
        if c[\"slug\"] == slug:
            count = await db.products.count_documents({\"category\": slug})
            return {**c, \"product_count\": count}
    raise HTTPException(status_code=404, detail=\"Category not found\")


# ---------- Routes: Products ----------
@api_router.get(\"/products\")
async def list_products(category: Optional[str] = None, q: Optional[str] = None,
                        featured: Optional[bool] = None, limit: int = 100):
    query: dict = {}
    if category:
        query[\"category\"] = category
    if featured is not None:
        query[\"featured\"] = featured
    if q:
        query[\"$or\"] = [
            {\"title\": {\"$regex\": q, \"$options\": \"i\"}},
            {\"description\": {\"$regex\": q, \"$options\": \"i\"}},
        ]
    cursor = db.products.find(query, {\"_id\": 0}).sort(\"created_at\", -1).limit(limit)
    items = await cursor.to_list(length=limit)
    for item in items:
        item[\"amazon_url\"] = with_affiliate_tag(item.get(\"amazon_url\", \"\"))
    return items


@api_router.get(\"/products/{product_id}\")
async def get_product(product_id: str):
    p = await db.products.find_one({\"id\": product_id}, {\"_id\": 0})
    if not p:
        raise HTTPException(status_code=404, detail=\"Product not found\")
    p[\"amazon_url\"] = with_affiliate_tag(p.get(\"amazon_url\", \"\"))
    return p


@api_router.post(\"/products\", status_code=201)
async def create_product(payload: ProductCreate, _: dict = Depends(require_admin)):
    if payload.category not in CATEGORY_SLUGS:
        raise HTTPException(status_code=400, detail=\"Unknown category\")
    doc = payload.model_dump()
    doc[\"id\"] = str(uuid.uuid4())
    doc[\"created_at\"] = datetime.now(timezone.utc).isoformat()
    await db.products.insert_one(doc)
    doc.pop(\"_id\", None)
    return doc


@api_router.patch(\"/products/{product_id}\")
async def update_product(product_id: str, payload: ProductUpdate, _: dict = Depends(require_admin)):
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if \"category\" in updates and updates[\"category\"] not in CATEGORY_SLUGS:
        raise HTTPException(status_code=400, detail=\"Unknown category\")
    if not updates:
        raise HTTPException(status_code=400, detail=\"No fields to update\")
    res = await db.products.update_one({\"id\": product_id}, {\"$set\": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail=\"Product not found\")
    p = await db.products.find_one({\"id\": product_id}, {\"_id\": 0})
    return p


@api_router.delete(\"/products/{product_id}\")
async def delete_product(product_id: str, _: dict = Depends(require_admin)):
    res = await db.products.delete_one({\"id\": product_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail=\"Product not found\")
    return {\"ok\": True}


@api_router.get(\"/\")
async def root():
    return {\"service\": \"4StarAbove Amazon Affiliate API\", \"affiliate_tag\": AFFILIATE_TAG}


# ---------- Startup ----------
async def seed_admin() -> None:
    existing = await db.users.find_one({\"email\": ADMIN_EMAIL.lower()})
    if existing is None:
        await db.users.insert_one({
            \"id\": str(uuid.uuid4()),
            \"email\": ADMIN_EMAIL.lower(),
            \"name\": \"Admin\",
            \"role\": \"admin\",
            \"password_hash\": hash_password(ADMIN_PASSWORD),
            \"created_at\": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(\"Seeded admin user %s\", ADMIN_EMAIL)
    elif not verify_password(ADMIN_PASSWORD, existing[\"password_hash\"]):
        await db.users.update_one({\"email\": ADMIN_EMAIL.lower()},
                                  {\"$set\": {\"password_hash\": hash_password(ADMIN_PASSWORD)}})
        logger.info(\"Updated admin password from env\")


SAMPLE_PRODUCTS = [
    (\"electronics\", \"Sony WH-1000XM5 Wireless Headphones\", \"Industry-leading noise cancellation with 30h battery life and crystal-clear calls.\", \"$348\", \"https://images.unsplash.com/photo-1583394838336-acd977736f90?w=800\", \"https://www.amazon.com/dp/B09XS7JWHH\", 4.8, True),
    (\"electronics\", \"Apple AirPods Pro (2nd Gen)\", \"Adaptive audio, active noise cancellation, and personalized spatial audio.\", \"$199\", \"https://images.unsplash.com/photo-1606220588913-b3aacb4d2f46?w=800\", \"https://www.amazon.com/dp/B0CHWRXH8B\", 4.7, True),
    (\"electronics\", \"Kindle Paperwhite (16 GB)\", \"Glare-free 6.8'' display, weeks of battery, warm light.\", \"$149\", \"https://images.unsplash.com/photo-1592434134753-a70baf7979d5?w=800\", \"https://www.amazon.com/dp/B09TMF6X23\", 4.6, False),
    (\"home\", \"Dyson V15 Detect Cordless Vacuum\", \"Laser dust detection & powerful suction — engineered for whole-home cleaning.\", \"$749\", \"https://images.unsplash.com/photo-1558317374-067fb5f30001?w=800\", \"https://www.amazon.com/dp/B08Z7RRWCG\", 4.7, True),
    (\"home\", \"Instant Pot Duo 7-in-1\", \"Pressure cook, slow cook, rice, steam, sauté, yogurt & warm.\", \"$99\", \"https://images.unsplash.com/photo-1585237017125-24baf8d7406f?w=800\", \"https://www.amazon.com/dp/B00FLYWNYQ\", 4.7, False),
    (\"home\", \"Philips Hue Smart Bulb Starter Kit\", \"16M colors, voice control, and immersive light scenes.\", \"$179\", \"https://images.unsplash.com/photo-1565374395542-0ce18882c857?w=800\", \"https://www.amazon.com/dp/B08LB2MTRQ\", 4.6, False),
    (\"fashion\", \"Levi's 501 Original Fit Jeans\", \"The blueprint of denim. Classic straight leg, button fly, iconic wear.\", \"$69\", \"https://images.unsplash.com/photo-1542272604-787c3835535d?w=800\", \"https://www.amazon.com/dp/B0793G9SL4\", 4.5, True),
    (\"fashion\", \"Ray-Ban Wayfarer Sunglasses\", \"A silhouette that never quits. Iconic acetate frames with UV protection.\", \"$154\", \"https://images.unsplash.com/photo-1572635196237-14b3f281503f?w=800\", \"https://www.amazon.com/dp/B00FKJEUI0\", 4.7, False),
    (\"fashion\", \"Casio Vintage A168 Watch\", \"Retro digital design in stainless steel — daily wear staple.\", \"$29\", \"https://images.unsplash.com/photo-1524592094714-0f0654e20314?w=800\", \"https://www.amazon.com/dp/B000GAWSDG\", 4.6, False),
    (\"books\", \"Atomic Habits — James Clear\", \"An easy & proven way to build good habits and break bad ones.\", \"$14\", \"https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=800\", \"https://www.amazon.com/dp/0735211299\", 4.8, True),
    (\"books\", \"The Psychology of Money\", \"Timeless lessons on wealth, greed, and happiness by Morgan Housel.\", \"$16\", \"https://images.unsplash.com/photo-1543002588-bfa74002ed7e?w=800\", \"https://www.amazon.com/dp/0857197681\", 4.7, False),
    (\"books\", \"Deep Work — Cal Newport\", \"Rules for focused success in a distracted world.\", \"$17\", \"https://images.unsplash.com/photo-1512820790803-83ca734da794?w=800\", \"https://www.amazon.com/dp/1455586692\", 4.6, False),
    (\"beauty\", \"CeraVe Hydrating Facial Cleanser\", \"Non-foaming, ceramide-rich cleanser for normal to dry skin.\", \"$16\", \"https://images.unsplash.com/photo-1556228720-195a672e8a03?w=800\", \"https://www.amazon.com/dp/B01N1LL62W\", 4.8, True),
    (\"beauty\", \"Dyson Airwrap Multi-Styler\", \"Curl, wave, smooth and dry with air — no extreme heat.\", \"$599\", \"https://images.unsplash.com/photo-1522337360788-8b13dee7a37e?w=800\", \"https://www.amazon.com/dp/B09CGDJVJ8\", 4.5, False),
    (\"beauty\", \"The Ordinary Niacinamide 10% + Zinc\", \"High-strength vitamin & mineral blemish formula.\", \"$8\", \"https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=800\", \"https://www.amazon.com/dp/B06VVL7NC5\", 4.6, False),
    (\"sports\", \"Bowflex SelectTech 552 Dumbbells\", \"Adjustable 5-52.5 lb — 15 sets in one, saves floor space.\", \"$429\", \"https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=800\", \"https://www.amazon.com/dp/B001ARYU58\", 4.8, True),
    (\"sports\", \"Manduka PROlite Yoga Mat\", \"Dense, dry-grip surface — built for lifetime practice.\", \"$99\", \"https://images.unsplash.com/photo-1601925260368-ae2f83cf8b7f?w=800\", \"https://www.amazon.com/dp/B002RHIRA6\", 4.7, False),
    (\"sports\", \"Hydro Flask 32 oz Water Bottle\", \"TempShield insulation keeps cold 24h, hot 12h.\", \"$44\", \"https://images.unsplash.com/photo-1523362628745-0c100150b504?w=800\", \"https://www.amazon.com/dp/B077JBQZPX\", 4.8, False),
]


async def seed_products() -> None:
    count = await db.products.count_documents({})
    if count > 0:
        return
    docs = []
    now = datetime.now(timezone.utc).isoformat()
    for cat, title, desc, price, img, url, rating, featured in SAMPLE_PRODUCTS:
        docs.append({
            \"id\": str(uuid.uuid4()),
            \"title\": title,
            \"description\": desc,
            \"price\": price,
            \"image_url\": img,
            \"amazon_url\": url,
            \"category\": cat,
            \"rating\": rating,
            \"featured\": featured,
            \"created_at\": now,
        })
    if docs:
        await db.products.insert_many(docs)
        logger.info(\"Seeded %d demo products\", len(docs))


@app.on_event(\"startup\")
async def startup() -> None:
    await db.users.create_index(\"email\", unique=True)
    await db.products.create_index(\"category\")
    await db.products.create_index(\"id\", unique=True)
    await seed_admin()
    await seed_products()


@app.on_event(\"shutdown\")
async def shutdown() -> None:
    client.close()


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get(\"CORS_ORIGINS\", \"*\").split(\",\"),
    allow_methods=[\"*\"],
    allow_headers=[\"*\"],
)
"
