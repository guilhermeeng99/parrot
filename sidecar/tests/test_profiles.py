"""Voice-profile CRUD, lock/unlock, usage, audio, delete null-out, resolution."""

from app.services import profiles as svc
from tests.conftest import make_profile


def test_create_list_get(client):
    pid = make_profile(client, name="  Alex  ")
    # name is trimmed on create
    assert client.get(f"/profiles/{pid}").json()["name"] == "Alex"
    rows = client.get("/profiles").json()
    assert len(rows) == 1 and rows[0]["id"] == pid


def test_create_rejects_blank_name(client):
    res = client.post(
        "/profiles",
        data={"name": "   "},
        files={"ref_audio": ("r.wav", b"x", "audio/wav")},
    )
    assert res.status_code == 400


def test_get_missing_is_404(client):
    res = client.get("/profiles/deadbeef")
    assert res.status_code == 404
    assert "deleted" in res.json()["detail"].lower()


def test_update_partial_patch(client):
    pid = make_profile(client)
    res = client.put(f"/profiles/{pid}", json={"ref_text": "new transcript"})
    assert res.status_code == 200
    assert res.json()["ref_text"] == "new transcript"
    assert res.json()["name"] == "Alex"  # untouched


def test_update_empty_body_is_400(client):
    pid = make_profile(client)
    assert client.put(f"/profiles/{pid}", json={}).status_code == 400


def test_update_whitespace_name_is_400_and_preserves(client):
    pid = make_profile(client, name="Keep")
    assert client.put(f"/profiles/{pid}", json={"name": "   "}).status_code == 400
    assert client.get(f"/profiles/{pid}").json()["name"] == "Keep"


def test_audio_fetch_serves_reference(client):
    pid = make_profile(client)
    res = client.get(f"/profiles/{pid}/audio")
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"


def test_audio_fetch_missing_file_is_404(client, env):
    pid = make_profile(client)
    # delete the reference file out-of-band
    (env / "parrot_data" / "voices").iterdir()
    for f in (env / "parrot_data" / "voices").iterdir():
        f.unlink()
    res = client.get(f"/profiles/{pid}/audio")
    assert res.status_code == 404
    assert "missing" in res.json()["detail"].lower()


def test_delete_nulls_history_not_cascade(client):
    pid = make_profile(client)
    gen = client.post("/generate", data={"text": "hello there", "profile_id": pid})
    assert gen.status_code == 200, gen.text
    # delete the profile → history survives with profile_id null
    assert client.delete(f"/profiles/{pid}").json() == {"deleted": pid}
    hist = client.get("/history").json()
    assert len(hist) == 1
    assert hist[0]["profile_id"] is None


def test_delete_missing_is_noop_success(client):
    assert client.delete("/profiles/nope1234").json() == {"deleted": "nope1234"}


def test_lock_then_unlock(client):
    pid = make_profile(client)
    gen = client.post("/generate", data={"text": "lock me", "profile_id": pid})
    hid = gen.headers["X-Audio-Id"]
    lock = client.post(f"/profiles/{pid}/lock", data={"history_id": hid, "seed": 42})
    assert lock.status_code == 200
    assert lock.json()["locked_audio_path"] == f"{pid}_locked.wav"
    prof = client.get(f"/profiles/{pid}").json()
    assert prof["is_locked"] == 1 and prof["seed"] == 42
    # unlock reverts
    assert client.post(f"/profiles/{pid}/unlock").json()["unlocked"] is True
    prof = client.get(f"/profiles/{pid}").json()
    assert prof["is_locked"] == 0 and prof["seed"] is None and prof["locked_audio_path"] == ""


def test_lock_missing_history_is_404(client):
    pid = make_profile(client)
    res = client.post(f"/profiles/{pid}/lock", data={"history_id": "ffffffff"})
    assert res.status_code == 404


def test_unlock_idempotent(client):
    pid = make_profile(client)
    assert client.post(f"/profiles/{pid}/unlock").status_code == 200
    assert client.post(f"/profiles/{pid}/unlock").status_code == 200


def test_usage_caps_and_counts(client):
    pid = make_profile(client)
    for _ in range(3):
        client.post("/generate", data={"text": "count me", "profile_id": pid})
    usage = client.get(f"/profiles/{pid}/usage").json()
    assert usage["synth_total"] == 3
    assert len(usage["synth_recent"]) == 3


# --- resolution unit tests (synthesis.md Profile Resolution) -----------------
def test_resolution_locked_wins(client):
    pid = make_profile(client)
    gen = client.post("/generate", data={"text": "x", "profile_id": pid})
    hid = gen.headers["X-Audio-Id"]
    client.post(f"/profiles/{pid}/lock", data={"history_id": hid, "seed": 7})
    r = svc.resolve_for_generate(pid, None, None, None, None, "Auto")
    assert r["ref_audio_path"].endswith(f"{pid}_locked.wav")
    assert r["seed"] == 7
    assert r["language"] is None  # Auto → None when a profile resolves


def test_resolution_unknown_profile_falls_through(client):
    r = svc.resolve_for_generate("nope9999", None, None, None, None, "en")
    assert r["resolved_profile_id"] is None
    assert r["ref_audio_path"] is None
    assert r["language"] == "en"  # untouched (no profile resolved)


def test_resolution_request_seed_overrides_profile(client):
    pid = make_profile(client)
    gen = client.post("/generate", data={"text": "x", "profile_id": pid})
    client.post(
        f"/profiles/{pid}/lock", data={"history_id": gen.headers["X-Audio-Id"], "seed": 7}
    )
    r = svc.resolve_for_generate(pid, None, None, None, 99, "Auto")
    assert r["seed"] == 99  # explicit request seed wins over profile's 7
