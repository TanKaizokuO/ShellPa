import os
import sqlite3
import pytest
from datetime import datetime
from shellpa.cheatsheet import manager

def test_init_db_creates_table(mock_shellpa_home):
    # Running get_connection initializes the DB and creates the table
    conn = manager.get_connection()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='snippets'")
    table = cursor.fetchone()
    assert table is not None
    assert table["name"] == "snippets"
    conn.close()

def test_add_snippet_returns_id(mock_shellpa_home):
    sid = manager.add_snippet("ls -la", "list directory contents", "file,list")
    assert sid == 1
    
    snippet = manager.get_snippet(sid)
    assert snippet["command"] == "ls -la"
    assert snippet["description"] == "list directory contents"
    assert snippet["tags"] == "file,list"
    assert snippet["source"] == "manual"
    assert snippet["use_count"] == 0
    assert snippet["last_used"] is None
    assert "created_at" in snippet

def test_get_all_snippets_empty(mock_shellpa_home):
    snippets = manager.get_all_snippets()
    assert snippets == []

def test_get_all_snippets_multiple(mock_shellpa_home):
    id1 = manager.add_snippet("echo 'hello'", "print hello")
    id2 = manager.add_snippet("rm -rf /", "scary command", "dangerous")
    
    snippets = manager.get_all_snippets()
    assert len(snippets) == 2
    assert any(s["id"] == id1 for s in snippets)
    assert any(s["id"] == id2 for s in snippets)

def test_get_snippet_by_id(mock_shellpa_home):
    sid = manager.add_snippet("pwd", "print working directory")
    snippet = manager.get_snippet(sid)
    assert snippet is not None
    assert snippet["command"] == "pwd"

def test_get_snippet_not_found(mock_shellpa_home):
    snippet = manager.get_snippet(999)
    assert snippet is None

def test_delete_snippet(mock_shellpa_home):
    sid = manager.add_snippet("pwd", "print working directory")
    assert manager.get_snippet(sid) is not None
    
    deleted = manager.delete_snippet(sid)
    assert deleted is True
    assert manager.get_snippet(sid) is None

def test_delete_snippet_not_found(mock_shellpa_home):
    deleted = manager.delete_snippet(999)
    assert deleted is False

def test_tag_snippet_appends(mock_shellpa_home):
    sid = manager.add_snippet("pwd", "print working dir", "nav")
    # Tag snippet with a new tag
    success = manager.tag_snippet(sid, "path")
    assert success is True
    
    snippet = manager.get_snippet(sid)
    assert snippet["tags"] == "nav,path"

def test_tag_snippet_duplicate_no_op(mock_shellpa_home):
    sid = manager.add_snippet("pwd", "print working dir", "nav,path")
    # Duplicate check should be case-insensitive and trim spaces
    success = manager.tag_snippet(sid, " NAV ")
    assert success is True
    
    snippet = manager.get_snippet(sid)
    # Tags list remains unchanged
    assert snippet["tags"] == "nav,path"

def test_tag_snippet_not_found(mock_shellpa_home):
    success = manager.tag_snippet(999, "test")
    assert success is False

def test_record_usage_increments(mock_shellpa_home):
    sid = manager.add_snippet("pwd", "print working dir")
    
    snippet_before = manager.get_snippet(sid)
    assert snippet_before["use_count"] == 0
    assert snippet_before["last_used"] is None
    
    manager.record_usage(sid)
    
    snippet_after = manager.get_snippet(sid)
    assert snippet_after["use_count"] == 1
    assert snippet_after["last_used"] is not None

def test_update_snippet(mock_shellpa_home):
    sid = manager.add_snippet("pwd", "old desc", "old,tags")
    
    # Update all fields
    success = manager.update_snippet(sid, "echo 'new'", "new desc", " new , tags , unique ")
    assert success is True
    
    snippet = manager.get_snippet(sid)
    assert snippet["command"] == "echo 'new'"
    assert snippet["description"] == "new desc"
    # Tags should be normalized: trimmed, deduped case-insensitively, joined
    assert snippet["tags"] == "new,tags,unique"
