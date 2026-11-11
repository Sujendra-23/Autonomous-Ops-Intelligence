#!/bin/sh
# Fresh identity database only. Password is a psql literal, never shell-expanded SQL.
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 --set app_password="$IDENTITY_APP_PASSWORD" <<'SQL'
CREATE ROLE identity_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER DATABASE identity_gateway OWNER TO identity_app;
ALTER SCHEMA public OWNER TO identity_app;
SQL
