"""
User Tools for Gemini Function Calling (v2.0)
==============================================

Scoop AI Tool Functions with EXPLICIT Parameter Passing.

v2.0 Architecture:
- All tool functions accept explicit user_id parameter
- NO ContextVar magic - explicit is better than implicit
- Works correctly with asyncio.to_thread (no context loss)
- ToolExecutor passes user_id to all function calls

Gemini SDK Function Calling:
- Define functions as Python callables
- Pass to GenerativeModel(tools=[...])
- Model calls function, gets result, generates response

FIX: Using sync PyMongo client instead of async Motor to avoid event loop conflicts
"""
import re
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from contextvars import ContextVar
import logging

logger = logging.getLogger(__name__)

# Store references (set by main.py on startup)
_user_store = None
_product_service = None
_db = None
# Sync MongoDB client for tool functions (avoids async loop conflicts)
_sync_db = None

# =============================================================================
# AFC PRODUCT CAPTURE (Bug Fix for /chat endpoint)
# =============================================================================
# When Gemini AFC mode is used, function call results are consumed internally
# and not accessible in the final response. This workaround captures products
# during search_products execution for retrieval after AFC completes.

_last_search_products: ContextVar[List[dict]] = ContextVar('last_search_products', default=[])


def get_last_search_products() -> List[dict]:
    """Get products captured during the last AFC search_products calls."""
    return _last_search_products.get([])


def clear_last_search_products():
    """Clear captured products before a new request."""
    _last_search_products.set([])


def _capture_product(product: dict):
    """Capture a product during AFC execution (internal use)."""
    current = _last_search_products.get([])
    current.append(product)
    _last_search_products.set(current)


def proto_to_native(obj: Any) -> Any:
    """
    Recursively convert Gemini protobuf types to native Python types.
    Fixes RepeatedComposite serialization errors when saving to MongoDB.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, 'items'):  # dict-like (MapComposite)
        return {k: proto_to_native(v) for k, v in obj.items()}
    if hasattr(obj, '__iter__'):  # list-like (RepeatedComposite)
        return [proto_to_native(item) for item in obj]
    # Fallback: convert to string
    return str(obj)


def set_stores(user_store=None, product_service=None, db=None, sync_db=None):
    """Set store references for tools to use"""
    global _user_store, _product_service, _db, _sync_db
    _user_store = user_store
    _product_service = product_service
    _db = db
    _sync_db = sync_db


# =============================================================================
# USER PROFILE TOOLS
# =============================================================================

def get_user_profile(user_id: str) -> dict:
    """
    Retrieve a user's profile including name, allergies, and preferences.

    v2.0: Requires explicit user_id parameter (no ContextVar magic).

    Call this when you need to:
    - Check user's allergies before recommending products
    - Get user's name for personalized responses
    - See user's fitness goals
    - Check purchase history

    Args:
        user_id: The user's unique identifier (REQUIRED)

    Returns:
        dict with keys: error, name, allergies, goals, preferences, stats
    """
    # DIAGNOSTIC: Log function call
    logger.info(f"🔍 get_user_profile CALLED (user_id={user_id})")
    
    if not user_id:
        logger.error("🔍 ERROR: No user_id provided! This is a v2.0 requirement.")
        return {
            "error": "user_id is required (v2.0 explicit parameter)",
            "name": None,
            "allergies": [],
            "goals": [],
            "preferences": {},
            "stats": {"total_messages": 0}
        }
    
    # Use sync MongoDB client to avoid async loop conflicts
    if _sync_db is None:
        logger.warning("🔍 No DB connection - returning empty profile")
        return {
            "error": None,
            "name": None,
            "allergies": [],
            "goals": [],
            "preferences": {},
            "stats": {"total_messages": 0}
        }

    try:
        logger.info(f"🔍 Querying MongoDB for user_id={user_id}")
        user = _sync_db.users.find_one({"user_id": user_id})
        
        if not user:
            logger.info(f"🔍 No user found in DB for {user_id} - returning empty profile")
            return {
                "error": None,
                "name": None,
                "allergies": [],
                "goals": [],
                "preferences": {},
                "stats": {"total_messages": 0}
            }

        profile = user.get("profile", {})
        physical_stats = user.get("physical_stats", {})
        name = profile.get("name")
        allergies = proto_to_native(profile.get("allergies", []))
        
        logger.info(f"🔍 Found user in DB: name={name}, allergies={allergies}")
        
        # Extract latest weight from weight_history
        current_weight = None
        weight_history = physical_stats.get("weight_history", [])
        if weight_history:
            from datetime import datetime
            latest = max(weight_history, key=lambda x: x.get("date", datetime.min))
            current_weight = latest.get("value")
        
        result = {
            "error": None,
            "name": name,
            "allergies": allergies,
            "goals": proto_to_native(profile.get("goals", [])),
            "preferences": proto_to_native(profile.get("preferences", {})),
            "stats": proto_to_native(user.get("stats", {})),
            "physical_stats": {
                "weight": current_weight,
                "height": physical_stats.get("height"),
                "age": proto_to_native(user.get("demographics", {})).get("age")
            }
        }
        
        logger.info(f"🔍 Returning profile: {result}")
        return result
    except Exception as e:
        logger.error(f"🔍 Error getting user profile: {e}")
        return {"error": str(e)}


def update_user_profile(
    user_id: str,
    name: Optional[str] = None,
    allergies: Optional[List[str]] = None,
    goals: Optional[List[str]] = None,
    fitness_level: Optional[str] = None
) -> dict:
    """
    Update a user's profile information.

    v2.0: Requires explicit user_id parameter (no ContextVar magic).

    Call this when user provides:
    - Their name ("მე ვარ გიორგი")
    - Allergy information ("ლაქტოზის აუტანლობა მაქვს")
    - Fitness goals ("მასის მომატება მინდა")
    - Experience level ("დამწყები ვარ")

    Args:
        user_id: The user's unique identifier (REQUIRED)
        name: User's name (optional)
        allergies: List of allergies like ["lactose", "gluten"] (optional)
        goals: List of goals like ["muscle_gain", "weight_loss"] (optional)
        fitness_level: One of "beginner", "intermediate", "advanced" (optional)

    Returns:
        dict with success status and updated profile
    """
    if not user_id:
        logger.error("🔍 ERROR: No user_id provided for update_user_profile")
        return {"success": False, "error": "user_id is required (v2.0 explicit parameter)"}
    
    logger.info(f"🔍 update_user_profile CALLED (auto user_id={user_id})")
    
    # Use sync MongoDB client to avoid async loop conflicts
    if _sync_db is None:
        return {"success": False, "error": "Database not connected"}

    try:
        profile_updates = {}
        if name is not None:
            profile_updates["name"] = proto_to_native(name)
        if allergies is not None:
            # Convert Gemini protobuf list to native Python list
            profile_updates["allergies"] = proto_to_native(allergies)
        if goals is not None:
            # Convert Gemini protobuf list to native Python list
            profile_updates["goals"] = proto_to_native(goals)
        if fitness_level is not None:
            profile_updates["fitness_level"] = proto_to_native(fitness_level)

        if not profile_updates:
            return {"success": False, "error": "No updates provided"}

        # Upsert user profile
        from datetime import datetime, timezone
        _sync_db.users.update_one(
            {"user_id": user_id},
            {
                "$set": {f"profile.{k}": v for k, v in profile_updates.items()},
                "$setOnInsert": {"user_id": user_id, "created_at": datetime.now(timezone.utc)}
            },
            upsert=True
        )

        return {
            "success": True,
            "message": "პროფილი განახლდა",
            "updated_fields": list(profile_updates.keys())
        }
    except Exception as e:
        logger.error(f"Error updating user profile: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# PRODUCT SEARCH TOOLS
# =============================================================================

def vector_search_products(
    query: str,
    max_price: Optional[float] = None,
    limit: int = 10
) -> dict:
    """
    Semantic search using MongoDB Atlas Vector Search.
    
    Uses Gemini text-embedding-004 to embed the query, then performs
    $vectorSearch against pre-computed description_embedding field.
    Falls back to regex search if vector search fails.
    
    Args:
        query: Search query in Georgian or English
        max_price: Maximum price in Georgian Lari (optional)
        limit: Maximum number of results (default 10)
    
    Returns:
        dict with products list, count, and search method used
    """
    if not query or not query.strip():
        return {"error": "საძიებო ტექსტი საჭიროა", "products": [], "count": 0}
    
    if _sync_db is None:
        logger.error("🔍 Vector search: Database not connected")
        return _fallback_to_regex_search(query, max_price)
    
    try:
        # Import new Gemini SDK for embedding
        from google import genai
        from config import settings
        
        # Create client
        client = genai.Client(api_key=settings.gemini_api_key)
        
        logger.info(f"🧠 Vector search: Embedding query '{query}'")
        
        # Generate embedding for query (768-dim)
        embedding_result = client.models.embed_content(
            model=settings.embedding_model,
            contents=query
        )
        query_vector = embedding_result.embeddings[0].values
        
        logger.info(f"🧠 Vector search: Got {len(query_vector)}-dim embedding")
        
        # Build $vectorSearch pipeline
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "vector_index",
                    "path": "description_embedding",
                    "queryVector": query_vector,
                    "numCandidates": 100,
                    "limit": limit
                }
            },
            {
                "$project": {
                    "id": 1,
                    "name": 1,
                    "name_ka": 1,
                    "brand": 1,
                    "price": 1,
                    "servings": 1,
                    "in_stock": 1,
                    "product_url": 1,
                    "score": {"$meta": "vectorSearchScore"}
                }
            }
        ]
        
        # Add price filter after vectorSearch
        if max_price:
            pipeline.insert(2, {"$match": {"price": {"$lte": max_price}}})
        
        # Execute aggregation
        products_cursor = _sync_db.products.aggregate(pipeline)
        products = list(products_cursor)
        
        logger.info(f"🧠 Vector search: Found {len(products)} products for '{query}'")
        
        if not products:
            logger.info("🧠 Vector search: No results, falling back to regex")
            return _fallback_to_regex_search(query, max_price)
        
        # Format results
        results = []
        for p in products:
            product_data = {
                "id": p.get("id"),
                "name": p.get("name_ka", p.get("name")),
                "brand": p.get("brand"),
                "price": p.get("price"),
                "servings": p.get("servings"),
                "in_stock": p.get("in_stock"),
                "url": p.get("product_url"),
                "score": p.get("score")  # Vector similarity score
            }
            results.append(product_data)
            # Capture for AFC mode
            _capture_product(product_data)
        
        return {
            "products": results,
            "count": len(results),
            "query": query,
            "method": "vector"
        }
        
    except Exception as e:
        logger.error(f"🧠 Vector search error: {e}")
        return _fallback_to_regex_search(query, max_price)


def _fallback_to_regex_search(query: str, max_price: Optional[float] = None) -> dict:
    """Internal fallback to regex search when vector search fails."""
    logger.info(f"📝 Fallback to regex search for '{query}'")
    return search_products(query=query, max_price=max_price)


def search_products(
    query: str = "",  # Default empty string for Gemini 3 sporadic bug
    category: Optional[str] = None,
    max_price: Optional[float] = None,
    in_stock_only: bool = False,
    user_id: Optional[str] = None,  # v2.0: explicit user_id (optional for search)
) -> dict:
    """
    Search for products in the Scoop.ge catalog.

    v2.0: Accepts explicit user_id parameter (optional for search, but passed by ToolExecutor).

    Call this when user asks about:
    - Specific products ("გინდა პროტეინი?")
    - Categories ("რა კრეატინები გაქვთ?")
    - Price-based queries ("100 ლარამდე რა არის?")
    - Brand searches ("Optimum Nutrition")

    Args:
        query: Search query in Georgian or English
        category: Filter by category (protein, creatine, bcaa, pre_workout, vitamin, gainer)
        max_price: Maximum price in Georgian Lari
        in_stock_only: Only show in-stock products (default True)
        user_id: User ID (optional, for future personalization)

    Returns:
        dict with products list and count
    """
    # Defensive check: Gemini 3 sometimes sends empty query
    # Allow category-only searches
    if not query or not query.strip():
        if category:
            query = category  # Use category as query when no explicit query
            logger.info(f"🔄 Empty query, using category as query: '{category}'")
        else:
            return {
                "error": "საძიებო ტექსტი საჭიროა",
                "products": [],
                "count": 0
            }

    # Use sync MongoDB client to avoid async loop conflicts
    if _sync_db is None:
        # Return mock data
        return {
            "products": [
                {
                    "id": "prod_001",
                    "name": "Gold Standard Whey",
                    "price": 159.99,
                    "brand": "Optimum Nutrition",
                    "in_stock": True
                }
            ],
            "count": 1,
            "query": query
        }

    try:
        products = []
        logger.info(f"🔎 search_products called with query='{query}'")
        
        # === NEW: Try Vector Search First (semantic) ===
        # Vector search provides better semantic matching for natural language queries
        try:
            from google import genai
            from config import settings
            
            client = genai.Client(api_key=settings.gemini_api_key)
            
            # Generate query embedding
            embedding_result = client.models.embed_content(
                model=settings.embedding_model,
                contents=query
            )
            query_vector = embedding_result.embeddings[0].values
            
            # Build vector search pipeline
            pipeline = [
                {
                    "$vectorSearch": {
                        "index": "vector_index",
                        "path": "description_embedding",
                        "queryVector": query_vector,
                        "numCandidates": 100,
                        "limit": 10
                    }
                }
            ]
            
            # Add price filter if specified
            if max_price:
                pipeline.append({"$match": {"price": {"$lte": max_price}}})
            
            if in_stock_only:
                pipeline.append({"$match": {"in_stock": True}})
            
            # Execute vector search
            products = list(_sync_db.products.aggregate(pipeline))
            logger.info(f"🧠 Vector search found {len(products)} products for '{query}'")
            
        except Exception as vec_err:
            logger.warning(f"🧠 Vector search failed: {vec_err}, falling back to regex")
            products = []  # Fall through to regex

        # === STEP 0: Translate Georgian to English FIRST ===
        # This translation is used for BOTH $text and $regex searches
        query_map = {
            # === Georgian to English translations ===
            # Proteins
            "პროტეინ": ["protein", "whey"],
            "ვეი": ["whey", "protein"],
            "იზოლატ": ["whey", "protein"],  # CHANGED: isolate not in DB, fallback to whey
            "კაზეინ": ["casein", "protein"],
            "ცილა": ["protein", "whey"],
            # Creatine
            "კრეატინ": ["creatine"],
            # Vitamins & Minerals
            "ვიტამინ": ["vitamin"],
            "მინერალ": ["mineral", "magnesium", "zinc", "calcium"],
            "ომეგა": ["omega"],
            "მაგნიუმ": ["magnesium"],
            "თუთია": ["zinc"],
            # Amino Acids
            "ამინო": ["amino", "bcaa", "eaa"],
            "bcaa": ["bcaa"],
            "eaa": ["eaa"],
            # Pre-workout & Energy
            "პრევორკაუთ": ["preworkout", "energy", "caffeine"],
            "პრე-ვორკაუტ": ["preworkout", "energy", "caffeine"],
            "ენერგ": ["energy", "caffeine"],
            "კოფეინ": ["caffeine"],
            # Mass & Weight Gainers
            "გეინერ": ["gainer", "mass"],
            "მასა": ["mass", "gainer"],
            "წონა": ["mass", "gainer"],
            # Recovery
            "აღდგენ": ["recovery", "glutamine"],
            "გლუტამინ": ["glutamine"],
            # Fat Burners
            "ცხიმ": ["fat", "carnitine"],
            "კარნიტინ": ["carnitine", "l-carnitine"],
            "ცხიმისმწველ": ["fat burner", "carnitine"],
            # Collagen
            "კოლაგენ": ["collagen"],
            # Sugar-free / Low-carb
            "შაქარ": ["zero", "sugar free"],
            "უშაქრო": ["zero", "sugar free"],
            "ნახშირწყალ": ["low carb", "zero carb"],
            # Brands (Georgian)
            "მუსლტექ": ["muscletech"],
            "დაიმატაიზ": ["dymatize"],
            "მიუტანტ": ["mutant"],
            
            # === ENGLISH SYNONYMS (for English queries) ===
            # These map English terms to what actually EXISTS in our database
            "isolate": ["whey", "protein"],  # isolate not in DB, use whey
            "iso": ["whey", "protein"],  # iso = isolate shorthand
            "vegan": ["plant", "ხორბლის პროტეინი"],  # vegan → plant protein
            "plant": ["plant", "ხორბლის პროტეინი"],
            "plant-based": ["plant", "ხორბლის პროტეინი"],
            "whey": ["whey", "protein"],
            "casein": ["casein", "protein"],
            "creatine": ["creatine"],
            "preworkout": ["preworkout", "energy"],
            "pre-workout": ["preworkout", "energy"],
            "bcaa": ["bcaa", "amino"],
            "amino": ["amino", "bcaa", "eaa"],
            "caffeine": ["caffeine", "energy"],
            "optimum": ["optimum", "optimum nutrition"],
            "muscletech": ["muscletech"],
            "dymatize": ["dymatize"],
            "mutant": ["mutant"],
            "applied": ["applied", "applied nutrition"],
            "grenade": ["grenade"],
        }

        # Check if query is Georgian and translate
        search_terms = [query.lower()]  # Default: use original query
        text_search_query = query  # For $text search
        for geo, eng_list in query_map.items():
            if geo in query.lower():
                search_terms = eng_list
                # For $text search, use the FIRST English term (most specific)
                text_search_query = eng_list[0]
                logger.info(f"🔄 Translated '{query}' → $text: '{text_search_query}', $regex terms: {eng_list}")
                break

        # === Phase 1: Try $text search first (indexed, ~10x faster) ===
        # Requires text index: db.products.createIndex({name: "text", name_ka: "text", brand: "text", category: "text"}, {default_language: "none"})
        # Skip $text entirely and go straight to $regex - more reliable
        # NOTE: $text search has cold start issues (returns 0 on first call)
        # $regex is slower but consistent
        # FIX: Don't reset products here - vector search results should persist!
        # products = []  # REMOVED: This was nullifying vector search results!

        # === Phase 2: Fallback to $regex ONLY if vector search found nothing ===
        if not products:
            logger.info(f"🔄 Vector search returned 0, falling back to regex for '{query}'")
            # Build MongoDB $or conditions for EACH search term
            or_conditions = []
            for term in search_terms:
                safe_term = re.escape(term)
                # English fields - case-insensitive
                or_conditions.append({"name": {"$regex": safe_term, "$options": "i"}})
                or_conditions.append({"brand": {"$regex": safe_term, "$options": "i"}})
                # ADDED: Search in keywords array (contains whey, protein, etc.)
                or_conditions.append({"keywords": {"$regex": safe_term, "$options": "i"}})

            # Also search Georgian fields with original query (no translation needed)
            safe_query = re.escape(query)
            or_conditions.append({"name_ka": {"$regex": safe_query}})
            or_conditions.append({"category": {"$regex": safe_query}})
            # ADDED: Keywords may contain Georgian terms too
            or_conditions.append({"keywords": {"$regex": safe_query}})

            mongo_query: Dict[str, Any] = {"$or": or_conditions}
            logger.info(f"📝 Regex search: {len(search_terms)} terms, {len(or_conditions)} conditions")

            # DISABLED: Category filter causes 0 results when Gemini passes wrong category format
            # The $or conditions already include category search via regex
            if category:
                logger.info(f"⚠️ Category filter ignored: '{category}' (using regex instead)")

            if max_price:
                mongo_query["price"] = {"$lte": max_price}

            if in_stock_only:
                mongo_query["in_stock"] = True

            # Sync query
            logger.info(f"🔍 MongoDB query: {mongo_query}")
            products = list(_sync_db.products.find(mongo_query).limit(10))
            logger.info(f"📝 $regex found {len(products)} products for '{query}'")
            if products:
                logger.info(f"📦 First product: name='{products[0].get('name')}', brand='{products[0].get('brand')}'")

        # Format results
        results = []
        for p in products:
            product_data = {
                "id": p.get("id"),
                "name": p.get("name_ka", p.get("name")),
                "brand": p.get("brand"),
                "price": p.get("price"),
                "servings": p.get("servings"),
                "in_stock": p.get("in_stock"),
                "url": p.get("product_url")
            }
            results.append(product_data)
            # Capture for AFC mode (products otherwise lost in /chat endpoint)
            _capture_product(product_data)

        return {
            "products": results,
            "count": len(results),
            "query": query
        }
    except Exception as e:
        logger.error(f"Error searching products: {e}")
        return {"error": str(e), "products": [], "count": 0}


def get_product_details(product_id: str) -> dict:
    """
    Get detailed information about a specific product.

    Call this when user wants more details about a product
    they've seen or been recommended.

    Args:
        product_id: The product ID (e.g., "prod_001")

    Returns:
        dict with full product details including description
    """
    # Use sync MongoDB client to avoid async loop conflicts
    if _sync_db is None:
        return {
            "error": "Database not connected",
            "product": None
        }

    try:
        product = _sync_db.products.find_one({"id": product_id})

        if not product:
            return {
                "error": f"პროდუქტი '{product_id}' ვერ მოიძებნა",
                "product": None
            }

        return {
            "error": None,
            "product": {
                "id": product.get("id"),
                "name": product.get("name"),
                "name_ka": product.get("name_ka"),
                "brand": product.get("brand"),
                "category": product.get("category"),
                "price": product.get("price"),
                "servings": product.get("servings"),
                "in_stock": product.get("in_stock"),
                "description": product.get("description"),
                "url": product.get("product_url")
            }
        }
    except Exception as e:
        logger.error(f"Error getting product details: {e}")
        return {"error": str(e), "product": None}


# =============================================================================
# TOOL LIST FOR GEMINI
# =============================================================================

# List of all tools to pass to Gemini
GEMINI_TOOLS = [
    get_user_profile,
    update_user_profile,
    search_products,
    get_product_details,
]


# =============================================================================
# ASYNC VERSIONS (For use with Gemini's async API)
# =============================================================================

async def async_get_user_profile(user_id: str) -> dict:
    """Async version of get_user_profile"""
    if _user_store is None:
        return {
            "error": None,
            "name": None,
            "allergies": [],
            "goals": [],
            "preferences": {},
            "stats": {"total_messages": 0}
        }

    user = await _user_store.get_user(user_id)
    if not user:
        return {
            "error": None,
            "name": None,
            "allergies": [],
            "goals": [],
            "preferences": {},
            "stats": {"total_messages": 0}
        }

    return {
        "error": None,
        "name": user.get("profile", {}).get("name"),
        "allergies": user.get("profile", {}).get("allergies", []),
        "goals": user.get("profile", {}).get("goals", []),
        "preferences": user.get("profile", {}).get("preferences", {}),
        "stats": user.get("stats", {})
    }


async def async_search_products(
    query: str = "",  # Default empty string for Gemini 3 sporadic bug
    category: Optional[str] = None,
    max_price: Optional[float] = None,
    in_stock_only: bool = True
) -> dict:
    """Async version of search_products"""
    # Defensive check: Gemini 3 sometimes sends empty query
    if not query or not query.strip():
        return {
            "error": "საძიებო ტექსტი საჭიროა",
            "products": [],
            "count": 0
        }

    if _db is None:
        return {"products": [], "count": 0, "query": query}

    query_map = {
        "პროტეინ": "protein",
        "კრეატინ": "creatine",
        "ვიტამინ": "vitamin",
    }

    search_term = query.lower()
    for geo, eng in query_map.items():
        if geo in search_term:
            search_term = eng
            break

    # SECURITY: escape regex special chars
    safe_term = re.escape(search_term)
    safe_query = re.escape(query)
    mongo_query = {
        "$or": [
            {"name": {"$regex": safe_term, "$options": "i"}},
            {"name_ka": {"$regex": safe_query, "$options": "i"}},
            {"brand": {"$regex": safe_term, "$options": "i"}},
            {"category": {"$regex": safe_term, "$options": "i"}},
        ]
    }

    if in_stock_only:
        mongo_query["in_stock"] = True
    if max_price:
        mongo_query["price"] = {"$lte": max_price}
    if category:
        mongo_query["category"] = category

    cursor = _db.products.find(mongo_query).limit(10)
    products = await cursor.to_list(length=10)

    results = [{
        "id": p.get("id"),
        "name": p.get("name_ka", p.get("name")),
        "brand": p.get("brand"),
        "price": p.get("price"),
        "in_stock": p.get("in_stock"),
        "url": p.get("product_url")
    } for p in products]

    return {"products": results, "count": len(results), "query": query}
