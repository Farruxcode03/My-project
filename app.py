
from flask import Flask, jsonify, request, send_from_directory
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import re
import os

app = Flask(__name__)

# ==========================================
# POSTGRESQL SOZLAMALARI
# ==========================================

DATABASE_URL=os.getenev("DATABASE_URL)


@contextmanager
def db_connection():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn:
            yield conn
    finally:
        conn.psycopg2.connect(DATABASE_URL)


# ==========================================
# YORDAMCHI FUNKSIYALAR
# ==========================================

NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


def valid_name(name):
    return isinstance(name, str) and bool(NAME_PATTERN.fullmatch(name))


def check_name(name):
    if not valid_name(name):
        raise ValueError("Jadval yoki ustun nomi noto‘g‘ri.")
    return name


def json_safe(value):
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
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
        SELECT column_name, data_type, is_nullable,
               column_default, is_identity
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position
    """, (table,))

    return cur.fetchall()


def get_primary_keys(cur, table):
    check_table(cur, table)

    cur.execute("""
        SELECT a.attname AS column_name
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = t.oid
         AND a.attnum = ANY(i.indkey)
        WHERE n.nspname = 'public'
          AND t.relname = %s
          AND i.indisprimary
        ORDER BY array_position(i.indkey, a.attnum)
    """, (table,))

    return [row["column_name"] for row in cur.fetchall()]


def get_table_names(cur):
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return [row["table_name"] for row in cur.fetchall()]


def get_count(cur, table):
    cur.execute(
        sql.SQL("SELECT COUNT(*) AS total FROM {}.{}").format(
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


# ==========================================
# HTML ASOSIY SAHIFA
# ==========================================

@app.route("/")
def home():
    return send_from_directory(app.root_path, "index.html")


# ==========================================
# DASHBOARD
# ==========================================

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    try:
        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
                    "database": DB_CONFIG["database"],
                    "tables": tables,
                    "total_tables": len(tables),
                    "total_rows": total_rows,
                    "table_info": table_info
                })

    except Exception as e:
        app.logger.exception("Dashboard xatosi")
        return error_response(e, 500)


# ==========================================
# JADVAL MA'LUMOTLARI
# ==========================================

@app.route("/api/data", methods=["GET"])
def get_table_data():
    table = request.args.get("table", "")

    try:
        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                columns = get_columns(cur, table)
                primary_keys = get_primary_keys(cur, table)

                cur.execute(
                    sql.SQL("SELECT * FROM {}.{}").format(
                        sql.Identifier("public"),
                        sql.Identifier(table)
                    )
                )

                rows = cur.fetchall()

                return jsonify({
                    "success": True,
                    "table": table,
                    "columns": [c["column_name"] for c in columns],
                    "rows": json_safe(rows),
                    "primary_key": primary_keys,
                    "editable_columns": get_editable_columns(
                        columns, primary_keys
                    )
                })

    except ValueError as e:
        return error_response(e, 404)
    except Exception as e:
        app.logger.exception("Jadval ma'lumotlarini olish xatosi")
        return error_response(e, 500)


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
            raise ValueError("Ustunlar ro‘yxat shaklida bo‘lishi kerak.")

        clean_columns = []

        for col in columns:
            col = check_name(col)

            if col.lower() == "id":
                raise ValueError("id ustuni avtomatik yaratiladi.")

            if col.lower() in [x.lower() for x in clean_columns]:
                raise ValueError("Takroriy ustun nomi: " + col)

            clean_columns.append(col)

        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:

                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s
                    ) AS exists
                """, (table,))

                if cur.fetchone()["exists"]:
                    raise ValueError("Bu jadval allaqachon mavjud.")

                definitions = [
                    sql.SQL("{} BIGSERIAL PRIMARY KEY").format(
                        sql.Identifier("id")
                    )
                ]

                for col in clean_columns:
                    definitions.append(
                        sql.SQL("{} TEXT").format(sql.Identifier(col))
                    )

                cur.execute(
                    sql.SQL("CREATE TABLE {}.{} ({})").format(
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
    except Exception as e:
        app.logger.exception("Jadval yaratish xatosi")
        return error_response(e, 500)


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
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                check_table(cur, table)

                if table == new_name:
                    return jsonify({
                        "success": True,
                        "table": new_name
                    })

                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s
                    ) AS exists
                """, (new_name,))

                if cur.fetchone()["exists"]:
                    raise ValueError("Yangi nom bilan jadval mavjud.")

                cur.execute(
                    sql.SQL("ALTER TABLE {}.{} RENAME TO {}").format(
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
    except Exception as e:
        app.logger.exception("Jadval nomini o'zgartirish xatosi")
        return error_response(e, 500)


# ==========================================
# JADVALNI O'CHIRISH
# ==========================================

@app.route("/api/tables/<table>", methods=["DELETE"])
def drop_table(table):
    try:
        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                check_table(cur, table)

                cur.execute(
                    sql.SQL("DROP TABLE {}.{}").format(
                        sql.Identifier("public"),
                        sql.Identifier(table)
                    )
                )

        return jsonify({
            "success": True,
            "message": "Jadval o‘chirildi.",
            "table": table
        })

    except ValueError as e:
        return error_response(e, 404)
    except Exception as e:
        app.logger.exception("Jadvalni o'chirish xatosi")
        return error_response(e, 500)


# ==========================================
# YANGI YOZUV QO'SHISH
# ==========================================

@app.route("/api/records", methods=["POST"])
def add_record():
    try:
        data = request.get_json(silent=True) or {}

        table = data.get("table", "")
        values = data.get("data", {})

        if not isinstance(values, dict):
            raise ValueError("Ma'lumotlar obyekt shaklida bo‘lishi kerak.")

        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                columns = get_columns(cur, table)
                primary_keys = get_primary_keys(cur, table)
                allowed = get_editable_columns(columns, primary_keys)

                clean_values = {
                    key: value
                    for key, value in values.items()
                    if key in allowed
                }

                if not clean_values:
                    cur.execute(
                        sql.SQL("INSERT INTO {}.{} DEFAULT VALUES").format(
                            sql.Identifier("public"),
                            sql.Identifier(table)
                        )
                    )
                else:
                    names = list(clean_values.keys())

                    placeholders = sql.SQL(", ").join(
                        sql.Placeholder() for _ in names
                    )

                    cur.execute(
                        sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                            sql.Identifier("public"),
                            sql.Identifier(table),
                            sql.SQL(", ").join(
                                sql.Identifier(x) for x in names
                            ),
                            placeholders
                        ),
                        [clean_values[x] for x in names]
                    )

        return jsonify({
            "success": True,
            "message": "Yozuv qo‘shildi."
        }), 201

    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        app.logger.exception("Yozuv qo'shish xatosi")
        return error_response(e, 500)


# ==========================================
# YOZUVNI TAHRIRLASH
# ==========================================

@app.route("/api/records/<record_id>", methods=["PUT"])
def update_record(record_id):
    try:
        table = request.args.get("table", "")
        data = request.get_json(silent=True) or {}
        values = data.get("data", {})

        if not isinstance(values, dict):
            raise ValueError("Ma'lumotlar obyekt shaklida bo‘lishi kerak.")

        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                columns = get_columns(cur, table)
                primary_keys = get_primary_keys(cur, table)

                if len(primary_keys) != 1:
                    raise ValueError(
                        "Tahrirlash uchun bitta PRIMARY KEY kerak."
                    )

                allowed = get_editable_columns(columns, primary_keys)

                clean_values = {
                    key: value
                    for key, value in values.items()
                    if key in allowed
                }

                if not clean_values:
                    raise ValueError("Tahrirlash uchun ustun topilmadi.")

                assignments = sql.SQL(", ").join(
                    sql.SQL("{} = {}").format(
                        sql.Identifier(key),
                        sql.Placeholder()
                    )
                    for key in clean_values
                )

                pk = primary_keys[0]

                cur.execute(
                    sql.SQL("UPDATE {}.{} SET {} WHERE {} = {}").format(
                        sql.Identifier("public"),
                        sql.Identifier(table),
                        assignments,
                        sql.Identifier(pk),
                        sql.Placeholder()
                    ),
                    list(clean_values.values()) + [record_id]
                )

                if cur.rowcount == 0:
                    raise ValueError("Yozuv topilmadi.")

        return jsonify({
            "success": True,
            "message": "Yozuv yangilandi."
        })

    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        app.logger.exception("Yozuv tahrirlash xatosi")
        return error_response(e, 500)


# ==========================================
# YOZUVNI O'CHIRISH
# ==========================================

@app.route("/api/records/<record_id>", methods=["DELETE"])
def delete_record(record_id):
    try:
        table = request.args.get("table", "")

        with db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                check_table(cur, table)

                primary_keys = get_primary_keys(cur, table)

                if len(primary_keys) != 1:
                    raise ValueError(
                        "O‘chirish uchun bitta PRIMARY KEY kerak."
                    )

                pk = primary_keys[0]

                cur.execute(
                    sql.SQL("DELETE FROM {}.{} WHERE {} = {}").format(
                        sql.Identifier("public"),
                        sql.Identifier(table),
                        sql.Identifier(pk),
                        sql.Placeholder()
                    ),
                    (record_id,)
                )

                if cur.rowcount == 0:
                    raise ValueError("Yozuv topilmadi.")

        return jsonify({
            "success": True,
            "message": "Yozuv o‘chirildi."
        })

    except ValueError as e:
        return error_response(e, 400)
    except Exception as e:
        app.logger.exception("Yozuv o'chirish xatosi")
        return error_response(e, 500)


# ==========================================
# SERVERNI ISHGA TUSHIRISH
# ==========================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
