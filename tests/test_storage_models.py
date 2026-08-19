"""Tests for storage models."""

from unified_chat.storage.models import Memory, UnifiedKV


def test_unified_kv_fields():
    kv = UnifiedKV(key="k", value="v")
    assert kv.key == "k"
    assert kv.value == "v"
    assert kv.updated_at is not None


def test_memory_has_kb_doc_id():
    m = Memory(content="x", kb_doc_id="doc1")
    assert m.kb_doc_id == "doc1"
    assert Memory(content="y").kb_doc_id is None
