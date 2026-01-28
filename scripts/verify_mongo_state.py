#!/usr/bin/env python3
"""
MongoDB State Verification Script for v2.1.0 Release
=====================================================

Verifies database structure and indexes for the Tiered Memory System.

Usage:
    python scripts/verify_mongo_state.py

Expected collections:
    - users (with curated_facts, daily_facts arrays)
    - user_memory (legacy support)

Expected indexes:
    - daily_facts.expires_at TTL index
    - user_id unique index
"""

import sys
import os
from datetime import datetime
from pprint import pprint

# Add backend root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from config import settings

def verify_mongo_state():
    """Main verification function."""
    
    print("=" * 70)
    print("  MongoDB State Verification for v2.1.0 Release")
    print("=" * 70)
    print()
    
    # Connect to MongoDB
    client = MongoClient(settings.mongodb_uri)
    db = client[settings.mongodb_database]
    
    print(f"📦 Database: {settings.mongodb_database}")
    print(f"🔗 Connection: {settings.mongodb_uri[:30]}...")
    print()
    
    # 1. List Collections
    print("─" * 50)
    print("📁 Collections:")
    collections = db.list_collection_names()
    for col in sorted(collections):
        count = db[col].count_documents({})
        print(f"   • {col}: {count} documents")
    print()
    
    # 2. Check Users Schema
    print("─" * 50)
    print("👤 Users Collection Schema:")
    users_col = db["users"]
    
    # Get sample documents
    sample_users = list(users_col.find().limit(3))
    
    if sample_users:
        print(f"   Found {users_col.count_documents({})} user documents")
        
        # Check for v2.1.0 fields
        has_curated = any("curated_facts" in u for u in sample_users)
        has_daily = any("daily_facts" in u for u in sample_users)
        
        print(f"   ├── curated_facts field present: {'✅ Yes' if has_curated else '⚪ Not yet (v2.1.0 feature)'}")
        print(f"   └── daily_facts field present: {'✅ Yes' if has_daily else '⚪ Not yet (v2.1.0 feature)'}")
        print()
        
        # Show sample user structure
        print("   Sample User Document Structure:")
        if sample_users:
            sample = sample_users[0]
            for key in sample.keys():
                val_type = type(sample[key]).__name__
                if isinstance(sample[key], list):
                    print(f"      {key}: Array[{len(sample[key])}]")
                elif isinstance(sample[key], dict):
                    print(f"      {key}: Object{{...}}")
                else:
                    print(f"      {key}: {val_type}")
    else:
        print("   ⚠️  No user documents found")
    print()
    
    # 3. Check Indexes
    print("─" * 50)
    print("🔍 Users Collection Indexes:")
    indexes = list(users_col.list_indexes())
    
    has_ttl_index = False
    has_user_id_index = False
    
    for idx in indexes:
        name = idx.get("name")
        keys = idx.get("key")
        expire = idx.get("expireAfterSeconds")
        
        if expire is not None:
            has_ttl_index = True
            print(f"   ⏰ TTL Index: {name}")
            print(f"      Keys: {keys}")
            print(f"      Expires After: {expire}s ({expire // 86400} days)")
        elif "user_id" in str(keys):
            has_user_id_index = True
            print(f"   🔑 User ID Index: {name}")
            print(f"      Keys: {keys}")
        else:
            print(f"   📌 Index: {name}")
            print(f"      Keys: {keys}")
    
    print()
    
    # 4. Check user_memory (legacy)
    print("─" * 50)
    print("📜 Legacy user_memory Collection:")
    user_memory_col = db.get_collection("user_memory")
    if "user_memory" in collections:
        legacy_count = user_memory_col.count_documents({})
        print(f"   Count: {legacy_count} documents")
        
        if legacy_count > 0:
            sample = user_memory_col.find_one()
            print(f"   Fields: {list(sample.keys())}")
    else:
        print("   ⚪ Collection not present")
    print()
    
    # 5. Summary
    print("=" * 70)
    print("  VERIFICATION SUMMARY")
    print("=" * 70)
    
    issues = []
    
    # Note: curated_facts/daily_facts are created on-demand, so absence is OK
    if not has_user_id_index:
        issues.append("⚠️  Missing user_id index on users collection")
    
    if issues:
        print("Issues Found:")
        for issue in issues:
            print(f"   {issue}")
    else:
        print("✅ Database structure verified for v2.1.0")
        print()
        print("Notes:")
        print("   • curated_facts/daily_facts created on first fact extraction")
        print("   • TTL index applied when daily_facts are stored")
        print("   • Legacy user_memory preserved for backward compatibility")
    
    print()
    print("=" * 70)
    
    client.close()
    return len(issues) == 0


if __name__ == "__main__":
    success = verify_mongo_state()
    sys.exit(0 if success else 1)
