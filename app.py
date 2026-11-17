import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import re
import os

app = Flask(name)

# ==========================================
# SUPABASE POSTGRESQL
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def db_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL Render Environment Variables ichida sozlanmagan."
        )

    conn = psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        connect_timeout=15
    )

    try:
        with conn:
            yield conn
    finally:
        conn.close()


# ==========================================
# YORDAMCHI FUNKSIYALAR
# ==========================================

NAME_PATTERN = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$"
)


def valid_name(name):
    return (
        isinstance(name, str)
        and bool(NAME_PATTERN.fullmatch(name))
    )


def check_name(name):
    if not valid_name(name):
        raise ValueError("Jadval yoki ustun nomi noto‘g‘ri.")
    return name


def json_safe(value):
    if isinstance(value, dict):
        return {
            key: json_safe(val)
            for key, val in value.items()
        }

    if isinstance(value, list):
        return [json_safe(item) for item in value]

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


def error_response(message, status=400):
    return jsonify({
        "success": False,
        "error": str(message)
    }), status


def server_error(error_name):
    app.logger.exception(error_name)
    return error_response(
        "Serverda xatolik yuz berdi. Render Logs bo‘limini tekshiring.",
        500
    )


def check_table(cur, table):
    check_name(table)

    cur.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
              AND table_type = 'BASE TABLE'
        ) AS exists
    """, (table,))

    result = cur.fetchone()

    if not result or not result["exists"]:
        raise ValueError("Bunday jadval topilmadi: " + table)


def get_columns(cur, table):
    check_table(cur, table)

    cur.execute("""
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default,
            is_identity
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position
    """, (table,))

    return cur.fetchall()


def get_primary_keys(cur, table):
    check_table(cur, table)

    cur.execute("""
        SELECT
            a.attname AS column_name,
            k.ordinality
        FROM pg_index i
        JOIN pg_class t
            ON t.oid = i.indrelid
        JOIN pg_namespace n
            ON n.oid = t.relnamespace
        CROSS JOIN LATERAL
            unnest(i.indkey) WITH ORDINALITY
            AS k(attnum, ordinality)
        JOIN pg_attribute a
            ON a.attrelid = t.oid
           AND a.attnum = k.attnum
        WHERE n.nspname = 'public'
          AND t.relname = %s
          AND i.indisprimary
        ORDER BY k.ordinality
    """, (table,))

    return [
        row["column_name"]
        for row in cur.fetchall()
    ]


def get_table_names(cur):
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)

    return [
        row["table_name"]
        for row in cur.fetchall()
    ]
    cur.execute(
        sql.SQL(
            "SELECT COUNT(*) AS total FROM {}.{}"
        ).format(
            sql.Identifier("public"),
            sql.Identifier(table)
        )
    )

    return cur.fetchone()["total"]


def get_editable_columns(columns, primary_keys):
    result = []

    for col in columns:
        name = col["column_name"]

        generated = (
            col["is_identity"] == "YES"
            or (
                col["column_default"] is not None
                and "nextval(" in str(col["column_default"])
            )
        )

        if name not in primary_keys and not generated:
            result.append(name)

    return result


def get_clean_values(values, allowed):
    return {
        key: value
        for key, value in values.items()
        if key in allowed
    }


# ==========================================
# ASOSIY SAHIFA
# ==========================================

@app.route("/")
def home():
    return send_from_directory(
        app.root_path,
        "index.html"
    )


# ==========================================
# SERVER TEKSHIRUVI
# ==========================================

@app.route("/health", methods=["GET"])
def health():
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

        return jsonify({
            "success": True,
            "server": "online",
            "database": "connected"
        })

    except Exception:
        return server_error("Health check xatosi")


# ==========================================
# DASHBOARD
# ==========================================

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    try:
        with db_connection() as conn:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:

                tables = get_table_names(cur)

                table_info = []
                total_rows = 0

                for table in tables:
                    count = get_count(cur, table)
                    total_rows += count

                    table_info.append({
                        "name": table,
                        "rows": count
                    })

                return jsonify({
                    "success": True,
                    "database": conn.info.dbname,
                    "tables": tables,
                    "total_tables": len(tables),
                    "total_rows": total_rows,
                    "table_info": table_info
                })

    except Exception:
        return server_error("Dashboard xatosi")


# ==========================================
# JADVAL MA'LUMOTLARINI OLISH
# ==========================================

@app.route("/api/data", methods=["GET"])
def get_table_data():
    table = request.args.get("table", "")

    try:
        with db_connection() as conn:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:

                columns = get_columns(cur, table)
                primary_keys = get_primary_keys(cur, table)

                cur.execute(
                    sql.SQL(
                        "SELECT * FROM {}.{}"
                    ).format(
                        sql.Identifier("public"),
                        sql.Identifier(table)
                    )
                )

                rows = cur.fetchall()

                return jsonify({
                    "success": True,
                    "table": table,
                    "columns": [
                        col["column_name"]
                        for col in columns
                    ],
                    "rows": json_safe(rows),
                    "primary_key": primary_keys,
                    "editable_columns": get_editable_columns(
                        columns,
                        primary_keys
                    )
                })

    except ValueError as e:
        return error_response(e, 404)
        return server_error("Jadval ma'lumotlarini olish xatosi")


# ==========================================
# YANGI JADVAL YARATISH
# ==========================================

@app.route("/api/tables", methods=["POST"])
def create_table():
    try:
        data = request.get_json(silent=True) or {}

        table = check_name(data.get("name", ""))
        columns = data.get("columns", [])

        if not isinstance(columns, list):
            raise ValueError(
                "Ustunlar ro‘yxat shaklida bo‘lishi kerak."
            )

        clean_columns = []

        for col in columns:
            col = check_name(col)

            if col.lower() == "id":
                raise ValueError(
                    "id ustuni avtomatik yaratiladi."
                )

            if col.lower() in [
                item.lower() for item in clean_columns
            ]:
                raise ValueError(
                    "Takroriy ustun nomi: " + col
                )

            clean_columns.append(col)

        with db_connection() as conn:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:

                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s
                    ) AS exists
                """, (table,))

                if cur.fetchone()["exists"]:
                    raise ValueError(
                        "Bu jadval allaqachon mavjud."
                    )

                definitions = [
                    sql.SQL(
                        "{} BIGSERIAL PRIMARY KEY"
                    ).format(
                        sql.Identifier("id")
                    )
                ]

                for col in clean_columns:
                    definitions.append(
                        sql.SQL("{} TEXT").format(
                            sql.Identifier(col)
                        )
                    )

                cur.execute(
                    sql.SQL(
                        "CREATE TABLE {}.{} ({})"
                    ).format(
                        sql.Identifier("public"),
                        sql.Identifier(table),
                        sql.SQL(", ").join(definitions)
                    )
                )

        return jsonify({
            "success": True,
            "message": "Jadval yaratildi.",
            "table": table
        }), 201

    except ValueError as e:
        return error_response(e, 400)

    except Exception:
        return server_error("Jadval yaratish xatosi")


# ==========================================
# JADVAL NOMINI O'ZGARTIRISH
# ==========================================

@app.route("/api/tables/<table>", methods=["PATCH"])
def rename_table(table):
    try:
        check_name(table)

        data = request.get_json(silent=True) or {}
        new_name = check_name(data.get("new_name", ""))

        with db_connection() as conn:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:

                check_table(cur, table)

                if table == new_name:
                    return jsonify({
                        "success": True,
                        "table": new_name
                    })

                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s
                    ) AS exists
                """, (new_name,))

                if cur.fetchone()["exists"]:
                    raise ValueError(
                        "Yangi nom bilan jadval mavjud."
                    )
                    sql.SQL(
                        "ALTER TABLE {}.{} RENAME TO {}"
                    ).format(
                        sql.Identifier("public"),
                        sql.Identifier(table),
                        sql.Identifier(new_name)
                    )
                )

        return jsonify({
            "success": True,
            "table": new_name,
            "message": "Jadval nomi o‘zgartirildi."
        })

    except ValueError as e:
        return error_response(e, 400)

    except Exception:
        return server_error("Jadval nomini o‘zgartirish xatosi")


# ==========================================
# JADVALNI O'CHIRISH
# ==========================================

@app.route("/api/tables/<table>", methods=["DELETE"])
def drop_table(table):
    try:
        with db_connection() as conn:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:

                check_table(cur, table)

                cur.execute(
                    sql.SQL(
                        "DROP TABLE {}.{}"
                    ).format(
                        sql.Identifier("public"),
                        sql.Identifier
