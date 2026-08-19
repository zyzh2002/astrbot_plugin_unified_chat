"""E2E contract checks for migration APIs (run inside Docker AstrBot).

Skipped outside AstrBot runtime.
"""

import pytest

kb_helper_mod = pytest.importorskip("astrbot.core.knowledge_base.kb_helper")
provider_entities = pytest.importorskip("astrbot.core.provider.entities")

KBHelper = kb_helper_mod.KBHelper


def test_migration_api_contract():
    for attr in (
        "list_documents",
        "get_chunks_by_doc_id",
        "delete_document",
        "upload_document",
    ):
        assert hasattr(KBHelper, attr), attr


def test_llm_response_contract():
    assert hasattr(provider_entities.LLMResponse, "completion_text")


def test_kb_manager_contract():
    from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

    assert hasattr(KnowledgeBaseManager, "get_kb_by_name")
    assert hasattr(KnowledgeBaseManager, "create_kb")
    assert hasattr(KnowledgeBaseManager, "retrieve")
