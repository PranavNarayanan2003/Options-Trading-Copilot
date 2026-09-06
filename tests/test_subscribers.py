from app.storage.db import AlertRepository

def test_subscribers_persist_and_unsubscribe(tmp_path):
    repo=AlertRepository(tmp_path/"state.sqlite3")
    repo.upsert_subscriber("123","pranav","P",True)
    repo.upsert_subscriber("456","friend","F",True)
    assert repo.subscriber_count()==2
    assert {x["chat_id"] for x in repo.active_subscribers()}=={"123","456"}
    repo.set_subscriber_active("123",False)
    assert repo.subscriber_count()==1
    repo2=AlertRepository(tmp_path/"state.sqlite3")
    assert repo2.subscriber_count()==1

def test_per_subscriber_telegram_message_mapping(tmp_path):
    repo=AlertRepository(tmp_path/"state.sqlite3")
    repo.save_telegram_message("alert1","111",99); repo.save_telegram_message("alert1","222",100)
    rows=repo.telegram_messages("alert1")
    assert {(r["chat_id"],r["message_id"]) for r in rows}=={("111",99),("222",100)}
