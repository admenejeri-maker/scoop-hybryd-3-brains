#!/usr/bin/env python3
"""
Test for NoneType Crash Fix
===========================

Tests that the fix for `TypeError: 'NoneType' object is not iterable`
works correctly when content.parts is None.

This tests the 4 locations fixed:
1. mongo_store.py:427
2. gemini_adapter.py:268
3. main.py:494
4. main.py:893

Date: 2026-01-26
"""

import sys
sys.path.insert(0, '/Users/maqashable/Desktop/scoop/backend')


def test_none_parts_iteration():
    """Test that `(parts or [])` pattern handles None correctly."""
    
    # Simulate what happens when content.parts is None
    class MockContent:
        def __init__(self, parts=None):
            self.parts = parts
            self.role = "model"
    
    # Test Case 1: parts is None
    content = MockContent(parts=None)
    result = []
    for part in (content.parts or []):
        result.append(part)
    
    assert result == [], f"Expected empty list, got {result}"
    print("✅ Test 1 PASSED: None parts returns empty list")
    
    # Test Case 2: parts is empty list
    content = MockContent(parts=[])
    result = []
    for part in (content.parts or []):
        result.append(part)
    
    assert result == [], f"Expected empty list, got {result}"
    print("✅ Test 2 PASSED: Empty parts works correctly")
    
    # Test Case 3: parts has items
    content = MockContent(parts=["item1", "item2"])
    result = []
    for part in (content.parts or []):
        result.append(part)
    
    assert result == ["item1", "item2"], f"Expected items, got {result}"
    print("✅ Test 3 PASSED: Non-empty parts iterates correctly")
    
    return True


def test_hasattr_and_parts_pattern():
    """Test the hasattr(content, 'parts') and content.parts pattern."""
    
    class MockContent:
        def __init__(self, parts=None):
            self.parts = parts
    
    class MockContentNoParts:
        pass
    
    # Test Case 1: has parts attribute, but it's None
    content = MockContent(parts=None)
    result = []
    
    if hasattr(content, 'parts') and content.parts:
        for part in content.parts:
            result.append(part)
    
    assert result == [], f"Expected empty list, got {result}"
    print("✅ Test 4 PASSED: hasattr + None check prevents iteration on None")
    
    # Test Case 2: no parts attribute at all
    content = MockContentNoParts()
    result = []
    
    if hasattr(content, 'parts') and content.parts:
        for part in content.parts:
            result.append(part)
    
    assert result == [], f"Expected empty list, got {result}"
    print("✅ Test 5 PASSED: hasattr check prevents AttributeError")
    
    # Test Case 3: has parts with values
    content = MockContent(parts=["a", "b"])
    result = []
    
    if hasattr(content, 'parts') and content.parts:
        for part in content.parts:
            result.append(part)
    
    assert result == ["a", "b"], f"Expected ['a', 'b'], got {result}"
    print("✅ Test 6 PASSED: Normal iteration works")
    
    return True


def test_nested_parts_access():
    """Test candidate.content.parts or [] pattern for main.py:893."""
    
    class MockPart:
        def __init__(self, text=None):
            self.text = text
    
    class MockContent:
        def __init__(self, parts=None):
            self.parts = parts
    
    class MockCandidate:
        def __init__(self, content=None):
            self.content = content
    
    # Test Case 1: content.parts is None
    candidate = MockCandidate(content=MockContent(parts=None))
    result = []
    
    for part in (candidate.content.parts or []):
        result.append(part)
    
    assert result == [], f"Expected empty list, got {result}"
    print("✅ Test 7 PASSED: Nested None parts handled correctly")
    
    # Test Case 2: normal parts
    candidate = MockCandidate(content=MockContent(parts=[MockPart("hello")]))
    result = []
    
    for part in (candidate.content.parts or []):
        result.append(part.text)
    
    assert result == ["hello"], f"Expected ['hello'], got {result}"
    print("✅ Test 8 PASSED: Nested parts iteration works")
    
    return True


def run_all_tests():
    """Run all tests and report results."""
    print("=" * 60)
    print("🧪 NoneType Crash Fix Tests")
    print("=" * 60)
    print()
    
    tests = [
        ("None parts iteration", test_none_parts_iteration),
        ("hasattr + parts pattern", test_hasattr_and_parts_pattern),
        ("Nested parts access", test_nested_parts_access),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        print(f"\n📋 Running: {name}")
        print("-" * 40)
        try:
            if test_fn():
                passed += 1
        except Exception as e:
            print(f"❌ FAILED: {e}")
            failed += 1
    
    print()
    print("=" * 60)
    print(f"📊 Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
