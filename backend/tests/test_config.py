from app.config import Settings, load_settings


def test_load_settings_reads_env(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "LLM_BASE_URL=https://api.groq.com/openai/v1\n"
        "LLM_MODEL=llama-3.3-70b-versatile\n"
        "LLM_API_KEY=gsk_test\n"
        "DB_HOST=localhost\nDB_PORT=5433\nDB_NAME=dq_audit\n"
        "DB_USER=postgres\nDB_PASSWORD=Gandy@09\n"
    )
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "DB_PORT", "DB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)

    s = load_settings(env_path=env)
    assert s.llm_model == "llama-3.3-70b-versatile"
    assert s.db_port == "5433"


def test_db_url_percent_encodes_password():
    s = Settings(
        llm_base_url="", llm_model="", llm_api_key="",
        db_host="localhost", db_port="5433", db_name="dq_audit",
        db_user="postgres", db_password="Gandy@09",
    )
    # the '@' in the password must not break the URL's host parsing
    assert "Gandy%40098" not in s.db_url  # sanity: no accidental digit smash
    assert "Gandy%4009@localhost:5433" in s.db_url
