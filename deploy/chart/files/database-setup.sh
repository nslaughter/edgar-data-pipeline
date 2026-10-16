#!/bin/sh
# Makes the record's tables and views in the database edgar, as its owner,
# and grants each role what it may do with them (spec/deployment.md, PG-8,
# PG-12, PG-13). The chart runs it as a hook Job after each install and
# upgrade, with the SQL it applies in $SQL, and the roles it grants to in
# $ROLES. Each file is applied only where what it makes doesn't exist, so it
# succeeds again once they do.
set -eu

SQL=${SQL:-/sql}

# Waits until the database accepts the owner's connection, and each role
# exists, which CloudNativePG makes once the database is up.
attempt=0
until [ "$(psql -X -Atc "SELECT count(*) FROM pg_roles WHERE rolname = ANY (string_to_array('$ROLES', ' '))" 2>/dev/null)" = "$(echo $ROLES | wc -w | tr -d ' ')" ]; do
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 120 ]; then
		echo "The database or its roles weren't ready" >&2
		exit 1
	fi
	sleep 5
done

psql -X -v ON_ERROR_STOP=1 -f "$SQL/setup.sql"
