"""Create schemas/grants using local backend admin credentials, without printing secrets."""

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.config import get_settings


def main():
    settings = get_settings()
    core, ai = make_url(settings.core_db_url), make_url(settings.ai_db_url)
    config = {}
    for line in (Path(__file__).resolve().parents[2] / "RentalManagement/.env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"').strip("'")
    password = config.get("MYSQL_ROOT_PASSWORD") or config.get("DB_PASSWORD")
    if not password:
        raise SystemExit("Missing local backend MYSQL_ROOT_PASSWORD/DB_PASSWORD.")
    if not core.database.replace("_", "").isalnum() or not ai.database.replace("_", "").isalnum():
        raise SystemExit("Invalid schema names")
    admin = create_engine(URL.create("mysql+pymysql", username="root", password=password,
                                    host=core.host, port=core.port or 3306), hide_parameters=True)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{ai.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        params = {"user": ai.username, "password": ai.password}
        connection.execute(text("CREATE USER IF NOT EXISTS :user@'%' IDENTIFIED BY :password"), params)
        connection.execute(text(f"GRANT ALL PRIVILEGES ON `{ai.database}`.* TO :user@'%'"), params)
        tables = ["listings", "rooms", "properties", "property_amenities", "room_amenities", "amenities",
                  "room_types", "provinces", "wards", "tenant_profiles", "tenant_profile_amenities",
                  "tenant_profile_wards", "listing_view_history", "saved_listings", "listing_contact_events",
                  "listing_search_history", "user_roles", "roles"]
        for table in tables:
            connection.execute(text(f"GRANT SELECT ON `{core.database}`.`{table}` TO :user@'%'"), params)
        connection.execute(text(f"GRANT SELECT (id,status,auth_version) ON `{core.database}`.users TO :user@'%'"), params)
    admin.dispose()
    print("AI schema and runtime grants ready; core remains read-only.")


if __name__ == "__main__":
    main()
