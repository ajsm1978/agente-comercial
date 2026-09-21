import os
import sqlite3
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template, request, session
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-render")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
SQLITE_PATH = os.getenv("SQLITE_PATH", "/tmp/agente_comercial_v2.db")

USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# In Render, never silently fall back to ephemeral SQLite. That would make
# company data disappear after a restart/deploy.
if os.getenv("RENDER") and not USE_POSTGRES:
    raise RuntimeError("DATABASE_URL não configurada: o serviço Render precisa usar PostgreSQL persistente.")

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row


def db():
    if USE_POSTGRES:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def q(conn, sql, params=()):
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
    return conn.execute(sql, params)


def init_db():
    conn = db()
    if USE_POSTGRES:
        q(conn, """CREATE TABLE IF NOT EXISTS companies (
            id BIGSERIAL PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            agent_name TEXT NOT NULL DEFAULT 'Assistente', presentation TEXT DEFAULT '',
            description TEXT DEFAULT '', instagram TEXT DEFAULT '', whatsapp TEXT DEFAULT '',
            phone TEXT DEFAULT '', email TEXT DEFAULT '')""")
        q(conn, """CREATE TABLE IF NOT EXISTS knowledge (
            id BIGSERIAL PRIMARY KEY, company_id BIGINT REFERENCES companies(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, content TEXT NOT NULL)""")
    else:
        q(conn, """CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            agent_name TEXT NOT NULL DEFAULT 'Assistente', presentation TEXT DEFAULT '',
            description TEXT DEFAULT '', instagram TEXT DEFAULT '', whatsapp TEXT DEFAULT '',
            phone TEXT DEFAULT '', email TEXT DEFAULT '')""")
        q(conn, """CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL,
            filename TEXT NOT NULL, content TEXT NOT NULL)""")
    conn.commit()
    conn.close()


def require_admin():
    return bool(session.get("admin"))


@app.errorhandler(Exception)
def handle_error(error):
    app.logger.exception("Unhandled application error")
    return "Erro interno do sistema. O erro foi registrado para correção.", 500


@app.get("/healthz")
def healthz():
    conn = db()
    companies_count = q(conn, "SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
    knowledge_count = q(conn, "SELECT COUNT(*) AS n FROM knowledge").fetchone()["n"]
    conn.close()
    return jsonify(
        ok=True,
        database="postgres" if USE_POSTGRES else "sqlite-test",
        companies=companies_count,
        knowledge=knowledge_count,
    )


@app.get("/")
def home():
    return redirect("/admin/login")


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return render_template("login.html", error="Senha inválida.")
    return render_template("login.html")


@app.get("/admin/logout")
def logout():
    session.clear()
    return redirect("/admin/login")


@app.get("/admin")
def admin():
    if not require_admin():
        return redirect("/admin/login")
    init_db()
    conn = db()
    companies = q(conn, "SELECT * FROM companies ORDER BY id DESC").fetchall()
    knowledge = {}
    for c in companies:
        rows = q(conn, "SELECT id,filename FROM knowledge WHERE company_id=? ORDER BY id DESC", (c["id"],)).fetchall()
        knowledge[c["id"]] = rows
    conn.close()
    return render_template("dashboard.html", companies=companies, knowledge=knowledge, use_postgres=USE_POSTGRES)


@app.post("/admin/company")
def create_company():
    if not require_admin():
        return redirect("/admin/login")
    init_db()
    f = request.form
    slug = f.get("slug", "").strip().lower().replace(" ", "-")
    name = f.get("name", "").strip()
    if not slug or not name:
        return "Nome e slug são obrigatórios.", 400
    conn = db()
    try:
        q(conn, """INSERT INTO companies
            (slug,name,agent_name,presentation,description,instagram,whatsapp,phone,email)
            VALUES (?,?,?,?,?,?,?,?,?)""",
          (slug, name, f.get("agent_name") or "Assistente",
           f.get("presentation", ""), f.get("description", ""),
           f.get("instagram", ""), f.get("whatsapp", ""),
           f.get("phone", ""), f.get("email", "")))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            conn.close()
            return "Este slug já está sendo usado. Escolha outro.", 400
        conn.close()
        raise
    conn.close()
    return redirect("/admin")


@app.route("/admin/company/<int:company_id>/edit", methods=["GET", "POST"])
def edit_company(company_id):
    if not require_admin():
        return redirect("/admin/login")
    init_db()
    conn = db()
    company = q(conn, "SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not company:
        conn.close()
        return "Empresa não encontrada.", 404
    if request.method == "POST":
        f = request.form
        slug = f.get("slug", "").strip().lower().replace(" ", "-")
        name = f.get("name", "").strip()
        if not slug or not name:
            conn.close()
            return "Nome e slug são obrigatórios.", 400
        try:
            q(conn, """UPDATE companies SET
                slug=?, name=?, agent_name=?, presentation=?, description=?,
                instagram=?, whatsapp=?, phone=?, email=? WHERE id=?""",
              (slug, name, f.get("agent_name") or "Assistente",
               f.get("presentation", ""), f.get("description", ""),
               f.get("instagram", ""), f.get("whatsapp", ""),
               f.get("phone", ""), f.get("email", ""), company_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                conn.close()
                return "Este slug já está sendo usado por outra empresa.", 400
            conn.close()
            raise
        conn.close()
        return redirect("/admin")
    conn.close()
    return render_template("edit_company.html", company=company)


@app.post("/admin/company/<int:company_id>/knowledge")
def add_knowledge(company_id):
    if not require_admin():
        return redirect("/admin/login")
    init_db()
    uploads = [u for u in request.files.getlist("files") if u and u.filename]
    if not uploads:
        return "Selecione pelo menos um arquivo.", 400
    allowed = {".txt", ".md", ".csv"}
    conn = db()
    exists = q(conn, "SELECT id FROM companies WHERE id=?", (company_id,)).fetchone()
    if not exists:
        conn.close()
        return "Empresa não encontrada.", 404
    for upload in uploads:
        ext = os.path.splitext(upload.filename)[1].lower()
        if ext not in allowed:
            conn.close()
            return f"Formato não permitido: {upload.filename}. Use .txt, .md ou .csv.", 400
        content = upload.read().decode("utf-8", "replace")
        q(conn, "INSERT INTO knowledge(company_id,filename,content) VALUES(?,?,?)",
          (company_id, upload.filename, content))
    conn.commit()
    conn.close()
    return redirect("/admin")


@app.post("/admin/company/<int:company_id>/knowledge/<int:knowledge_id>/delete")
def delete_knowledge(company_id, knowledge_id):
    if not require_admin():
        return redirect("/admin/login")
    init_db()
    conn = db()
    q(conn, "DELETE FROM knowledge WHERE id=? AND company_id=?", (knowledge_id, company_id))
    conn.commit()
    conn.close()
    return redirect("/admin")


@app.get("/c/<slug>")
def public_chat(slug):
    init_db()
    conn = db()
    company = q(conn, "SELECT * FROM companies WHERE slug=?", (slug,)).fetchone()
    conn.close()
    if not company:
        return "Empresa não encontrada.", 404
    return render_template("chat.html", company=company)


@app.post("/api/chat/<slug>")
def api_chat(slug):
    init_db()
    conn = db()
    company = q(conn, "SELECT * FROM companies WHERE slug=?", (slug,)).fetchone()
    if not company:
        conn.close()
        return jsonify(error="Empresa não encontrada."), 404
    docs = q(conn, "SELECT filename,content FROM knowledge WHERE company_id=?", (company["id"],)).fetchall()
    conn.close()

    message = (request.json or {}).get("message", "").strip()
    if not message:
        return jsonify(reply="Pode me dizer o que você precisa?")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return jsonify(reply="O atendimento por IA ainda não está configurado. Entre em contato com a equipe da empresa.")

    context = [
        f"Empresa: {company['name']}", f"Agente: {company['agent_name']}",
        f"Apresentação: {company['presentation']}", f"Descrição: {company['description']}",
        f"Instagram: {company['instagram']}", f"WhatsApp: {company['whatsapp']}",
        f"Telefone: {company['phone']}", f"E-mail: {company['email']}",
    ]
    context += [f"Arquivo {d['filename']}: {d['content']}" for d in docs]

    system = f"""Você é {company['agent_name']}, assistente comercial de {company['name']}.
Responda em português, de forma natural, curta e útil.
Use somente as informações do contexto abaixo.
Nunca invente preços, promoções, estoque, horários ou condições.
Quando pedirem orçamento ou preço, encaminhe para a equipe e indique o Instagram quando houver.
Se o assunto não tiver relação com a empresa, diga que você não possui essa informação e ofereça contato humano.
CONTEXTO:
{chr(10).join(context)}"""

    try:
        response = OpenAI(api_key=api_key).chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": message}],
        )
        answer = response.choices[0].message.content or "Não consegui responder agora."
    except Exception:
        app.logger.exception("OpenAI request failed")
        answer = "Não consegui concluir o atendimento agora. Fale diretamente com a equipe da empresa."
    return jsonify(reply=answer)


if __name__ == "__main__":
    app.logger.info("Database backend: %s", "postgres" if USE_POSTGRES else "sqlite-test")
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
