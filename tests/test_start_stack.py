import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.start_stack import parse_env_file, is_mysql_enabled, build_compose_command


def test_parse_env_file_reads_key_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=test",
                "MYSQL_URL=mysql+mysqlconnector://user:pass@db/name",
                "# comment",
                "DB_PATH=./data/db/bot_database.db",
            ]
        ),
        encoding="utf-8",
    )

    env = parse_env_file(str(env_file))

    assert env["TELEGRAM_BOT_TOKEN"] == "test"
    assert env["MYSQL_URL"].startswith("mysql+mysqlconnector://")
    assert env["DB_PATH"] == "./data/db/bot_database.db"


def test_is_mysql_enabled_true_when_mysql_url_set():
    assert is_mysql_enabled({"MYSQL_URL": "mysql+mysqlconnector://telegram:telegram@db/telegram_chatbot"})


def test_is_mysql_enabled_false_when_mysql_url_missing():
    assert not is_mysql_enabled({})
    assert not is_mysql_enabled({"MYSQL_URL": "   "})


def test_build_compose_command_with_mysql_profile():
    cmd = build_compose_command(mysql_enabled=True)
    assert cmd[:5] == ["docker", "compose", "--profile", "mysql", "up"]
    assert "-d" in cmd
    assert "--build" in cmd


def test_build_compose_command_without_mysql_profile():
    cmd = build_compose_command(mysql_enabled=False, detached=False, build=False)
    assert cmd == ["docker", "compose", "up"]
