"""
MongoDB Store for Conversation Persistence
==========================================

ANSWER TO QUESTION #2: MongoDB Architecture

Schema Design Recommendation: OPTION B - Separate Collections
Reason: Better query performance, independent scaling, cleaner TTL policies

Collections:
1. conversations - Chat history with session management
2. users - User profiles (allergies, preferences, name)

This approach allows:
- Independent indexes on each collection
- Different TTL policies (conversations: 7 days, users: never expire)
- Easier aggregation queries
- Better scalability

ANSWER TO QUESTION #1: History Format
chat.history returns list of Content objects:
[
    Content(role="user", parts=[Part(text="...")]),
    Content(role="model", parts=[Part(text="..."), Part(function_call=...)])
]

We store as Native BSON (not JSON string) because:
- Better query performance with MongoDB
- Native date handling
- Smaller storage footprint
- Easier to query specific messages
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import IndexModel, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, OperationFailure

# Gemini SDK types for reference (New SDK)
try:
    from google.genai.types import Content, Part, UserContent, ModelContent
except ImportError:
    Content = Any
    Part = Any
    UserContent = Any
    ModelContent = Any

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA DEFINITIONS
# =============================================================================

"""
CONVERSATIONS COLLECTION SCHEMA:
{
    "_id": ObjectId,
    "session_id": str,              # Unique session identifier
    "user_id": str,                 # User identifier (can be anonymous)
    "history": [                    # Gemini chat history (Native BSON)
        {
            "role": "user" | "model",
            "parts": [
                {"text": str},
                {"function_call": {"name": str, "args": dict}},
                {"function_response": {"name": str, "response": dict}}
            ]
        }
    ],
    "message_count": int,           # For quick filtering
    "token_estimate": int,          # Estimated tokens (for pruning decisions)
    "summary": str | null,          # Summarized context when history was pruned
    "summary_created_at": datetime | null,  # When summary was generated (WEEK 1)
    "summary_expires_at": datetime | null,  # Summary TTL: 30 days (WEEK 1)
    "metadata": {
        "language": "ka",           # User language preference
        "last_topic": str,          # Last discussed topic
        "products_viewed": [str],   # Product IDs user has seen
        "products_recommended": [str]  # Product IDs recommended
    },
    "created_at": datetime,
    "updated_at": datetime,
    "expires_at": datetime          # TTL index field (7 days default)
}

USERS COLLECTION SCHEMA:
{
    "_id": ObjectId,
    "user_id": str,                 # Primary identifier (unique)
    "profile": {
        "name": str | null,         # User's name if provided
        "allergies": [str],         # ["lactose", "gluten", "soy"]
        "goals": [str],             # ["muscle_gain", "weight_loss"]
        "preferences": {
            "max_price": float | null,
            "preferred_brands": [str],
            "flavor_preferences": [str]
        },
        "fitness_level": str | null  # "beginner" | "intermediate" | "advanced"
    },
    "stats": {
        "total_sessions": int,
        "total_messages": int,
        "products_purchased": [str],
        "last_purchase_date": datetime | null
    },
    "created_at": datetime,
    "updated_at": datetime
}

INDEXES:
- conversations:
  - (user_id, created_at DESC) - For loading recent sessions
  - (session_id) unique - For direct session access
  - (expires_at) TTL - Auto-delete after 7 days

- users:
  - (user_id) unique - Primary lookup
"""


@dataclass
class ConversationDocument:
    """Conversation document model"""
    session_id: str
    user_id: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    message_count: int = 0
    token_estimate: int = 0
    summary: Optional[str] = None
    summary_created_at: Optional[datetime] = None  # WEEK 1: Summary timestamp
    summary_expires_at: Optional[datetime] = None  # WEEK 1: Summary TTL (30 days)
    metadata: Dict[str, Any] = field(default_factory=lambda: {
        "language": "ka",
        "last_topic": None,
        "products_viewed": [],
        "products_recommended": []
    })
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(days=7))


@dataclass
class UserDocument:
    """
    User document model - Enhanced with hybrid memory system
    
    Structure:
    - profile: Core user info (name, allergies, goals, preferences)
    - demographics: Age, gender, occupation (structured facts)
    - physical_stats: Height, weight history (versioned)
    - lifestyle: Workout frequency, experience, sleep
    - user_facts: Semantic memory with vector embeddings
    - stats: Usage statistics
    """
    user_id: str
    
    # Core profile (existing)
    profile: Dict[str, Any] = field(default_factory=lambda: {
        "name": None,
        "allergies": [],
        "goals": [],
        "preferences": {
            "max_price": None,
            "preferred_brands": [],
            "flavor_preferences": []
        },
        "fitness_level": None
    })
    
    # NEW: Demographics (structured facts)
    demographics: Dict[str, Any] = field(default_factory=lambda: {
        "age": None,                    # int - for dosage recommendations
        "gender": None,                 # str - "male" | "female" | "other"
        "occupation": None,             # str - free text, e.g., "ბანკირი"
        "occupation_category": None     # str - "sedentary" | "light" | "active" | "heavy"
    })
    
    # NEW: Physical stats (versioned for tracking)
    physical_stats: Dict[str, Any] = field(default_factory=lambda: {
        "height": None,                 # float - in cm
        "weight_history": [],           # List[{"value": float, "date": datetime, "note": str}]
        "body_fat_percent": None        # float - optional
    })
    
    # NEW: Lifestyle (for recommendation context)
    lifestyle: Dict[str, Any] = field(default_factory=lambda: {
        "workout_frequency": None,      # int - times per week
        "experience_years": None,       # int - fitness experience
        "sleep_hours": None,            # float - average sleep
        "activity_level": None          # str - "sedentary" | "moderate" | "active"
    })
    
    # NEW: Semantic memory (user facts with vector embeddings)
    # Example: [{"fact": "მუხლი მტკივა წვიმიან ამინდში", "embedding": [...], ...}]
    user_facts: List[Dict[str, Any]] = field(default_factory=list)
    # Each fact structure:
    # {
    #     "fact": str,                  # The fact text
    #     "embedding": List[float],     # 768-dim vector for similarity search
    #     "created_at": datetime,       # When the fact was learned
    #     "importance_score": float,    # 0.0-1.0, for pruning/prioritization
    #     "source": str,                # "user_stated" | "inferred"
    #     "is_sensitive": bool          # True for pregnancy, medical conditions, etc.
    # }
    
    # Usage stats (existing)
    stats: Dict[str, Any] = field(default_factory=lambda: {
        "total_sessions": 0,
        "total_messages": 0,
        "products_purchased": [],
        "last_purchase_date": None
    })
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


# =============================================================================
# DATABASE MANAGER
# =============================================================================

class DatabaseManager:
    """Singleton database connection manager"""

    _instance: Optional["DatabaseManager"] = None
    _client: Optional[AsyncIOMotorClient] = None
    _db: Optional[AsyncIOMotorDatabase] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def connect(self, uri: str, database: str) -> None:
        """Initialize MongoDB connection with recommended settings"""
        if self._client is not None:
            return

        self._client = AsyncIOMotorClient(
            uri,
            # Connection Pool Settings
            minPoolSize=1,
            maxPoolSize=10,
            # Timeouts
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            serverSelectionTimeoutMS=5000,
            # Retry Settings
            retryWrites=True,
            retryReads=True,
        )
        self._db = self._client[database]

        # Verify connection
        try:
            await self._client.admin.command("ping")
            logger.info(f"Connected to MongoDB: {database}")
        except ConnectionFailure as e:
            logger.error(f"MongoDB connection failed: {e}")
            raise

        # Create indexes
        await self._create_indexes()

    async def _create_indexes(self) -> None:
        """Create recommended indexes for optimal query performance"""

        # ANSWER TO QUESTION #2: Indexing Recommendations

        # Conversations indexes
        conv_indexes = [
            # Primary lookup: Get user's recent conversations
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            # Session lookup: Direct access by session_id
            IndexModel([("session_id", ASCENDING)], unique=True),
            # TTL index: Auto-delete raw messages after 7 days
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
            # WEEK 1: TTL index for summaries (30 days retention)
            IndexModel([("summary_expires_at", ASCENDING)], expireAfterSeconds=0),
        ]

        # Users indexes
        user_indexes = [
            # Primary lookup
            IndexModel([("user_id", ASCENDING)], unique=True),
        ]

        try:
            await self._db.conversations.create_indexes(conv_indexes)
            await self._db.users.create_indexes(user_indexes)
            logger.info("MongoDB indexes created successfully")
        except OperationFailure as e:
            logger.warning(f"Index creation warning: {e}")

    async def disconnect(self) -> None:
        """Close database connection"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
            logger.info("Disconnected from MongoDB")

    @property
    def db(self) -> AsyncIOMotorDatabase:
        """Get database instance"""
        if self._db is None:
            raise RuntimeError("Database not connected")
        return self._db

    async def ping(self) -> bool:
        """Health check"""
        try:
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False


# Global instance
db_manager = DatabaseManager()


# =============================================================================
# CONVERSATION STORE
# =============================================================================

class ConversationStore:
    """
    Handles conversation history persistence

    ANSWER TO QUESTION #1: Memory Persistence Implementation

    Features:
    - Load/save Gemini chat history
    - Token-based pruning with summarization
    - Sliding window for very long conversations
    - Multi-session support (user can return after 2 days)
    """

    def __init__(self, max_messages: int = 100, max_tokens: int = 50000):
        """
        Args:
            max_messages: Trigger summarization when exceeded (sliding window)
            max_tokens: Estimated token limit before pruning
        """
        self.max_messages = max_messages
        self.max_tokens = max_tokens

    @property
    def collection(self):
        return db_manager.db.conversations

    # -------------------------------------------------------------------------
    # History Format Conversion
    # -------------------------------------------------------------------------

    def gemini_to_bson(self, history: List[Content]) -> List[Dict[str, Any]]:
        """
        ANSWER TO QUESTION #1: chat.history format

        Convert Gemini Content objects to BSON-storable format
        
        WEEK 2 FIX: New SDK's get_history() returns dicts, not Content objects!
        Handle both formats for compatibility.

        Gemini history structure:
        [
            Content(role="user", parts=[Part(text="გამარჯობა")]),
            Content(role="model", parts=[
                Part(text="გამარჯობა! რით დაგეხმარო?"),
                Part(function_call=FunctionCall(name="search", args={...}))
            ])
        ]
        """
        def proto_to_native(obj):
            """Recursively convert protobuf types to native Python types"""
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

        bson_history = []

        for content in history:
            # WEEK 2 FIX: Handle dict format from get_history()
            if isinstance(content, dict):
                # Already in dict format, just ensure it's native Python
                entry = {
                    "role": content.get("role", "user"),
                    "parts": content.get("parts", [])
                }
                # Convert any nested protobuf objects
                entry["parts"] = proto_to_native(entry["parts"])
                if entry["parts"]: # Only add if there are parts
                    bson_history.append(entry)
                continue
            
            # Original logic for Content objects
            # Determine role from content type (new SDK) or role attribute (old SDK)
            if hasattr(content, 'role'):
                role = content.role
            elif content.__class__.__name__ == 'UserContent':
                role = 'user'
            elif content.__class__.__name__ == 'ModelContent':
                role = 'model'
            else:
                role = 'user'  # Default fallback

            entry = {
                "role": role,
                "parts": []
            }

            for part in (content.parts or []):
                if hasattr(part, "text") and part.text:
                    entry["parts"].append({"text": part.text})
                elif hasattr(part, "function_call") and part.function_call:
                    # Use proto_to_native for robust conversion
                    args_dict = proto_to_native(part.function_call.args) if part.function_call.args else {}
                    
                    entry["parts"].append({
                        "function_call": {
                            "name": part.function_call.name,
                            "args": args_dict
                        }
                    })
                elif hasattr(part, "function_response") and part.function_response:
                    # Use proto_to_native for robust conversion
                    response_data = proto_to_native(part.function_response.response) if part.function_response.response else None
                    
                    entry["parts"].append({
                        "function_response": {
                            "name": part.function_response.name,
                            "response": response_data
                        }
                    })

            if entry["parts"]:  # Only add if there are parts
                bson_history.append(entry)

        return bson_history

    def bson_to_gemini(self, bson_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert BSON history back to Gemini-compatible format

        Note: Returns dicts that Gemini SDK accepts for history parameter.
        The SessionManager._bson_to_sdk_history() method handles conversion
        to UserContent/ModelContent objects required by the new SDK.

        This method is kept for backwards compatibility and intermediate format.
        """
        # Return raw BSON history - SessionManager handles SDK type conversion
        return bson_history

    def estimate_tokens(self, history: List[Dict[str, Any]]) -> int:
        """Rough token estimation (4 chars ~= 1 token)"""
        try:
            import json
            text = json.dumps(history, default=str)  # Use str() for non-serializable
            return len(text) // 4
        except Exception:
            # Fallback: estimate from string representation
            return len(str(history)) // 4

    # -------------------------------------------------------------------------
    # CRUD Operations
    # -------------------------------------------------------------------------

    async def load_history(
        self,
        user_id: str,
        session_id: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], str, Optional[str]]:
        """
        Load conversation history for a user

        ANSWER TO QUESTION #1: Multi-session support
        - If session_id provided: Load that specific session
        - If not: Load most recent session for user (within 7 days)
        - If user returns after 2 days, their history is preserved!

        Returns:
            tuple: (history, session_id, summary)
        """
        query = {"user_id": user_id}
        if session_id:
            query["session_id"] = session_id

        # Get most recent session
        doc = await self.collection.find_one(
            query,
            sort=[("updated_at", DESCENDING)]
        )

        if doc:
            return (
                doc.get("history", []),
                doc["session_id"],
                doc.get("summary")
            )

        # No existing session - create new
        import uuid
        new_session_id = f"session_{uuid.uuid4().hex[:12]}"
        return ([], new_session_id, None)

    async def save_history(
        self,
        user_id: str,
        session_id: str,
        history: List[Content],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Save conversation history with automatic pruning

        ANSWER TO QUESTION #1: Token Limit Management

        Strategy:
        1. If messages > max_messages: Apply sliding window
        2. If tokens > max_tokens: Summarize older messages
        3. Keep summary in separate field for context
        """
        bson_history = self.gemini_to_bson(history)
        token_estimate = self.estimate_tokens(bson_history)
        message_count = len(bson_history)

        # Check if pruning needed
        summary = None
        if message_count > self.max_messages or token_estimate > self.max_tokens:
            bson_history, summary = await self._prune_history(bson_history)
            token_estimate = self.estimate_tokens(bson_history)
            message_count = len(bson_history)

        # Update or insert
        update_doc = {
            "$set": {
                "user_id": user_id,
                "session_id": session_id,
                "history": bson_history,
                "message_count": message_count,
                "token_estimate": token_estimate,
                "updated_at": datetime.utcnow(),
                "expires_at": datetime.utcnow() + timedelta(days=7),
            },
            "$setOnInsert": {
                "created_at": datetime.utcnow(),
            }
        }

        if summary:
            update_doc["$set"]["summary"] = summary
            # WEEK 1: Set summary TTL (30 days)
            update_doc["$set"]["summary_created_at"] = datetime.utcnow()
            update_doc["$set"]["summary_expires_at"] = datetime.utcnow() + timedelta(days=30)

        if metadata:
            update_doc["$set"]["metadata"] = metadata

        await self.collection.update_one(
            {"session_id": session_id},
            update_doc,
            upsert=True
        )

    async def _prune_history(
        self,
        history: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], str]:
        """
        ANSWER TO QUESTION #1: History pruning strategies

        Options implemented:
        1. Sliding window: Keep last N messages
        2. Summarization: Generate summary of pruned messages
        3. Context pruning: Remove less important messages

        Current strategy: Sliding window + Summary
        """
        # Keep last 30 messages (15 exchanges) - reduced from 50 to prevent context confusion
        keep_count = 30

        if len(history) <= keep_count:
            return history, None

        # Messages to summarize
        old_messages = history[:-keep_count]
        new_messages = history[-keep_count:]

        # Generate summary of old messages
        # In production, you might call Gemini to summarize
        summary = self._generate_simple_summary(old_messages)

        logger.info(f"Pruned history: {len(old_messages)} messages summarized")

        return new_messages, summary

    def _generate_simple_summary(self, messages: List[Dict[str, Any]]) -> str:
        """
        Generate a simple summary of conversation

        For production: Call Gemini with summarization prompt
        """
        topics = []
        products_mentioned = []

        for msg in messages:
            for part in msg.get("parts", []):
                text = part.get("text", "")
                # Extract key topics (simplified)
                if "პროტეინ" in text.lower():
                    topics.append("პროტეინი")
                if "კრეატინ" in text.lower():
                    topics.append("კრეატინი")
                if "ალერგია" in text.lower():
                    topics.append("ალერგია")

        unique_topics = list(set(topics))

        return f"წინა საუბარში განხილული: {', '.join(unique_topics) if unique_topics else 'ზოგადი კითხვები'}"

    # -------------------------------------------------------------------------
    # History Retrieval for Frontend
    # -------------------------------------------------------------------------

    async def get_user_sessions(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get list of user's recent sessions for sidebar display.
        
        Returns list of session summaries with title extracted from first message.
        """
        cursor = self.collection.find(
            {"user_id": user_id},
            {
                "session_id": 1, 
                "created_at": 1, 
                "updated_at": 1, 
                "message_count": 1, 
                "history": {"$slice": 1}  # Only first message for title
            }
        ).sort("updated_at", DESCENDING).limit(limit)
        
        sessions = []
        async for doc in cursor:
            # Extract title from first user message
            title = "ახალი საუბარი"
            if doc.get("history"):
                first_msg = doc["history"][0]
                if first_msg.get("role") == "user":
                    for part in first_msg.get("parts", []):
                        if "text" in part:
                            text = part["text"]
                            title = text[:30] + "..." if len(text) > 30 else text
                            break
            
            sessions.append({
                "session_id": doc["session_id"],
                "title": title,
                "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
                "updated_at": doc["updated_at"].isoformat() if doc.get("updated_at") else None,
                "message_count": doc.get("message_count", 0)
            })
        
        return sessions

    async def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get formatted message history for a specific session.
        
        Converts internal BSON format to frontend-friendly format:
        [{"role": "user"|"assistant", "content": "..."}]
        """
        doc = await self.collection.find_one({"session_id": session_id})
        if not doc:
            return []
        
        messages = []
        for entry in doc.get("history", []):
            # Convert 'model' role to 'assistant' for frontend
            role = "assistant" if entry["role"] == "model" else entry["role"]
            
            # Extract text from parts
            text = ""
            for part in entry.get("parts", []):
                if "text" in part:
                    text = part["text"]
                    break
            
            if text:
                messages.append({
                    "role": role,
                    "content": text
                })
        
        return messages

    async def clear_session(self, session_id: str) -> bool:
        """Delete a specific session"""
        result = await self.collection.delete_one({"session_id": session_id})
        return result.deleted_count > 0

    async def clear_user_sessions(self, user_id: str) -> int:
        """Delete all sessions for a user"""
        result = await self.collection.delete_many({"user_id": user_id})
        return result.deleted_count


# =============================================================================
# USER STORE
# =============================================================================

class UserStore:
    """
    Handles user profile persistence

    Stores:
    - User name
    - Allergies
    - Goals and preferences
    - Purchase history
    """

    @property
    def collection(self):
        return db_manager.db.users

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user profile"""
        doc = await self.collection.find_one({"user_id": user_id})
        return doc

    async def create_or_update_user(
        self,
        user_id: str,
        profile_updates: Optional[Dict[str, Any]] = None,
        stats_updates: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create or update user profile"""
        update_doc = {
            "$set": {
                "updated_at": datetime.utcnow()
            },
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": datetime.utcnow(),
                "profile": {
                    "name": None,
                    "allergies": [],
                    "goals": [],
                    "preferences": {
                        "max_price": None,
                        "preferred_brands": [],
                        "flavor_preferences": []
                    },
                    "fitness_level": None
                },
                "stats": {
                    "total_sessions": 0,
                    "total_messages": 0,
                    "products_purchased": [],
                    "last_purchase_date": None
                }
            }
        }

        if profile_updates:
            for key, value in profile_updates.items():
                update_doc["$set"][f"profile.{key}"] = value

        if stats_updates:
            for key, value in stats_updates.items():
                if key in ["total_sessions", "total_messages"]:
                    update_doc.setdefault("$inc", {})[f"stats.{key}"] = value
                else:
                    update_doc["$set"][f"stats.{key}"] = value

        result = await self.collection.find_one_and_update(
            {"user_id": user_id},
            update_doc,
            upsert=True,
            return_document=True
        )

        return result

    async def add_allergy(self, user_id: str, allergy: str) -> None:
        """Add allergy to user profile"""
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$addToSet": {"profile.allergies": allergy.lower()},
                "$set": {"updated_at": datetime.utcnow()}
            },
            upsert=True
        )

    async def set_user_name(self, user_id: str, name: str) -> None:
        """Set user's name"""
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "profile.name": name,
                    "updated_at": datetime.utcnow()
                }
            },
            upsert=True
        )

    async def increment_stats(self, user_id: str, messages: int = 1) -> None:
        """Increment user message count"""
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$inc": {"stats.total_messages": messages},
                "$set": {"updated_at": datetime.utcnow()}
            },
            upsert=True
        )

    async def delete_user(self, user_id: str) -> bool:
        """Delete user profile (GDPR Right to Erasure)"""
        result = await self.collection.delete_one({"user_id": user_id})
        return result.deleted_count > 0

    # =========================================================================
    # NEW: Enhanced Profile Methods
    # =========================================================================

    async def update_demographics(
        self, 
        user_id: str, 
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update demographics using dot-notation (atomic).
        
        Args:
            user_id: User identifier
            updates: Dict with keys: age, gender, occupation, occupation_category
            
        Example:
            await user_store.update_demographics(user_id, {
                "age": 40,
                "occupation": "ბანკირი",
                "occupation_category": "sedentary"
            })
        """
        set_updates = {"updated_at": datetime.utcnow()}
        
        valid_keys = {"age", "gender", "occupation", "occupation_category"}
        for key, value in updates.items():
            if key in valid_keys:
                set_updates[f"demographics.{key}"] = value
        
        result = await self.collection.update_one(
            {"user_id": user_id},
            {"$set": set_updates},
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None

    async def update_physical_stats(
        self, 
        user_id: str, 
        height: Optional[float] = None,
        body_fat_percent: Optional[float] = None
    ) -> bool:
        """
        Update physical stats (height, body_fat).
        For weight, use add_weight_entry() for versioning.
        """
        set_updates = {"updated_at": datetime.utcnow()}
        
        if height is not None:
            set_updates["physical_stats.height"] = height
        if body_fat_percent is not None:
            set_updates["physical_stats.body_fat_percent"] = body_fat_percent
        
        result = await self.collection.update_one(
            {"user_id": user_id},
            {"$set": set_updates},
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None

    async def add_weight_entry(
        self, 
        user_id: str, 
        weight: float, 
        note: Optional[str] = None
    ) -> bool:
        """
        Add weight entry to history (versioned, not overwriting).
        
        Example:
            await user_store.add_weight_entry(user_id, 85.5, "დილით")
        """
        weight_entry = {
            "value": weight,
            "date": datetime.utcnow(),
            "note": note
        }
        
        result = await self.collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"physical_stats.weight_history": weight_entry},
                "$set": {"updated_at": datetime.utcnow()}
            },
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None

    async def update_lifestyle(
        self, 
        user_id: str, 
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update lifestyle fields.
        
        Args:
            updates: Dict with keys: workout_frequency, experience_years, 
                     sleep_hours, activity_level
        """
        set_updates = {"updated_at": datetime.utcnow()}
        
        valid_keys = {"workout_frequency", "experience_years", "sleep_hours", "activity_level"}
        for key, value in updates.items():
            if key in valid_keys:
                set_updates[f"lifestyle.{key}"] = value
        
        result = await self.collection.update_one(
            {"user_id": user_id},
            {"$set": set_updates},
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None

    async def add_user_fact(
        self,
        user_id: str,
        fact: str,
        embedding: List[float],
        importance_score: float = 0.5,
        source: str = "user_stated",
        is_sensitive: bool = False
    ) -> Dict[str, Any]:
        """
        Add semantic fact with deduplication (Cosine > 0.90 = duplicate).
        
        Args:
            user_id: User identifier
            fact: The fact text (min 10 chars)
            embedding: 768-dim vector
            importance_score: 0.0-1.0, default 0.5
            source: "user_stated" or "inferred"
            is_sensitive: True for pregnancy, medical conditions, etc.
            
        Returns:
            {"status": "added" | "duplicate" | "error", "message": str}
        """
        # Validation
        if len(fact) < 10:
            return {"status": "error", "message": "Fact too short (min 10 chars)"}
        
        if len(embedding) != 768:
            return {"status": "error", "message": f"Invalid embedding dim: {len(embedding)}, expected 768"}
        
        # Check for duplicates using cosine similarity
        user_doc = await self.get_user(user_id)
        
        if user_doc and user_doc.get("user_facts"):
            for existing_fact in user_doc["user_facts"]:
                existing_embedding = existing_fact.get("embedding", [])
                if existing_embedding:
                    similarity = self._cosine_similarity(embedding, existing_embedding)
                    if similarity > 0.90:
                        return {
                            "status": "duplicate",
                            "message": f"Similar fact exists (similarity: {similarity:.2f}): {existing_fact['fact'][:50]}..."
                        }
        
        # Add new fact
        fact_doc = {
            "fact": fact,
            "embedding": embedding,
            "created_at": datetime.utcnow(),
            "importance_score": importance_score,
            "source": source,
            "is_sensitive": is_sensitive
        }
        
        result = await self.collection.update_one(
            {"user_id": user_id},
            {
                "$push": {"user_facts": fact_doc},
                "$set": {"updated_at": datetime.utcnow()}
            },
            upsert=True
        )
        
        if result.modified_count > 0 or result.upserted_id is not None:
            return {"status": "added", "message": f"Fact added: {fact[:50]}..."}
        return {"status": "error", "message": "Failed to add fact"}

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)

    async def get_relevant_facts(
        self,
        user_id: str,
        query_embedding: List[float],
        limit: int = 5,
        min_similarity: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Get relevant facts using vector similarity search.
        
        Args:
            user_id: User identifier
            query_embedding: 768-dim query vector
            limit: Max facts to return
            min_similarity: Minimum cosine similarity threshold
            
        Returns:
            List of facts sorted by similarity (descending)
        """
        user_doc = await self.get_user(user_id)
        
        if not user_doc or not user_doc.get("user_facts"):
            return []
        
        # Calculate similarity for each fact
        facts_with_similarity = []
        for fact in user_doc["user_facts"]:
            embedding = fact.get("embedding", [])
            if embedding:
                similarity = self._cosine_similarity(query_embedding, embedding)
                if similarity >= min_similarity:
                    facts_with_similarity.append({
                        "fact": fact["fact"],
                        "similarity": round(similarity, 3),
                        "importance_score": fact.get("importance_score", 0.5),
                        "is_sensitive": fact.get("is_sensitive", False),
                        "created_at": fact.get("created_at")
                    })
        
        # Sort by similarity (descending) and return top N
        facts_with_similarity.sort(key=lambda x: x["similarity"], reverse=True)
        return facts_with_similarity[:limit]

    async def get_full_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get complete user profile for system prompt injection.
        
        Returns structured profile without embeddings (for context).
        """
        user_doc = await self.get_user(user_id)
        
        if not user_doc:
            return None
        
        # Build profile summary (without embeddings for LLM context)
        profile = {
            "name": user_doc.get("profile", {}).get("name"),
            "demographics": user_doc.get("demographics", {}),
            "physical_stats": {
                "height": user_doc.get("physical_stats", {}).get("height"),
                "current_weight": None,
                "body_fat_percent": user_doc.get("physical_stats", {}).get("body_fat_percent")
            },
            "lifestyle": user_doc.get("lifestyle", {}),
            "allergies": user_doc.get("profile", {}).get("allergies", []),
            "goals": user_doc.get("profile", {}).get("goals", []),
            "fitness_level": user_doc.get("profile", {}).get("fitness_level"),
            "facts_count": len(user_doc.get("user_facts", []))
        }
        
        # Get latest weight from history
        weight_history = user_doc.get("physical_stats", {}).get("weight_history", [])
        if weight_history:
            latest_weight = max(weight_history, key=lambda x: x.get("date", datetime.min))
            profile["physical_stats"]["current_weight"] = latest_weight.get("value")
        
        return profile

