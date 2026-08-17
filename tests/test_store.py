"""存储层单测：用户认证 / 会话持久化 / 反馈 / badcase 流转"""
from docmind import store


def test_user_lifecycle(temp_db):
    assert store.create_user("u1", "pw1")
    assert not store.create_user("u1", "pw2")      # 重复创建失败
    assert store.verify_user("u1", "pw1")
    assert not store.verify_user("u1", "wrong")
    assert store.set_password("u1", "newpw")
    assert store.verify_user("u1", "newpw")
    assert not store.verify_user("ghost", "x")
    assert store.delete_user("u1")
    assert store.list_users() == []


def test_admin_role(temp_db):
    store.create_user("u1", "pw")
    assert not store.is_admin("u1")
    assert store.set_admin("u1", True)
    assert store.is_admin("u1")
    store.set_admin("u1", False)
    assert not store.is_admin("u1")
    assert not store.is_admin("ghost")


def test_seed_admin_is_admin(temp_db):
    store.ensure_seed_admin()
    assert store.is_admin("admin")


def test_session_messages(temp_db):
    seq0 = store.append_message("s1", "user", "问题", user="alice")
    seq1 = store.append_message("s1", "assistant", "回答", raw="干净回答", user="alice")
    assert (seq0, seq1) == (0, 1)
    msgs = store.load_session("s1")
    assert msgs == [{"role": "user", "content": "问题"},
                    {"role": "assistant", "content": "回答"}]
    assert store.session_owner("s1") == "alice"
    assert store.session_owner("ghost-session") is None
    assert store.load_session("ghost") == []


def test_session_title_from_first_user_msg(temp_db):
    store.append_message("s2", "user", "第一个问题很长长长长", user="bob")
    sessions = store.list_all_sessions()
    s2 = next(s for s in sessions if s["id"] == "s2")
    assert s2["title"] == "第一个问题很长长长长"[:30]
    assert s2["user"] == "bob"


def test_feedback_upsert(temp_db):
    store.append_message("s1", "user", "q", user="u")
    store.append_message("s1", "assistant", "a", user="u")
    store.save_feedback("s1", 1, "up")
    assert store.get_feedback("s1") == {"1": "up"}
    store.save_feedback("s1", 1, "down")           # 重复评价覆盖
    assert store.get_feedback("s1") == {"1": "down"}


def test_badcase_flow(temp_db):
    store.create_user("asker", "pw")
    store.append_message("s1", "user", "为什么会这样？", user="asker")
    store.append_message("s1", "assistant", "回答内容节选", user="asker")
    store.save_feedback("s1", 1, "down")
    cases = store.list_badcases()
    assert len(cases) == 1
    c = cases[0]
    assert c["question"] == "为什么会这样？"
    assert c["answer_excerpt"].startswith("回答内容")
    assert c["user"] == "asker"
    assert c["status"] == "pending"
    # 流转：标记已解决 + 备注
    store.set_badcase_status(c["id"], "resolved", "已核实")
    c2 = store.list_badcases()[0]
    assert c2["status"] == "resolved" and c2["note"] == "已核实"
    # 待处理计数归零
    assert store.stats_overview()["badcase_pending"] == 0


def test_stats_overview(temp_db):
    store.create_user("u", "pw")
    store.append_message("s1", "user", "q", user="u")
    store.append_message("s1", "assistant", "a", user="u")
    store.save_feedback("s1", 1, "up")
    ov = store.stats_overview()
    assert ov["users"] == 1
    assert ov["sessions"] == 1
    assert ov["messages"] == 2
    assert ov["feedback_up"] == 1
    assert ov["feedback_down"] == 0
