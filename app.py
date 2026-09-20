import os
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from flask import Flask, request, redirect, session, render_template, jsonify
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev')
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()

if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row

DB_READY = False


def normalize_database_url(url):
    """Normalize common Supabase connection-string variants."""
    if not url:
        return url
    url = url.strip()
    try:
        # Supabase/Render may occasionally concatenate connection fields.
        # Example:
        # ...@db.example.supabase.coport=5432database=postgresuser=postgres
        marker = ".supabase.coport="
        if marker in url:
            prefix, tail = url.split(marker, 1)
            if "database=" in tail:
                port, tail = tail.split("database=", 1)
            else:
                port, tail = tail, ""
            if "user=" in tail:
                database, user = tail.split("user=", 1)
            else:
                database, user = tail, "postgres"
            port = port.strip() or "5432"
            database = database.strip() or "postgres"
            user = user.strip() or "postgres"
            return f"{prefix}.supabase.co:{port}/{database}?user={user}"

        # Also repair the equivalent form if a scheme/host is otherwise intact.
        if ".supabase.coport" in url:
            url = url.replace(".supabase.coport", ".supabase.co:")
            url = url.replace("database=", "/", 1)
            url = url.replace("user=", "?user=", 1)

        parts = urlsplit(url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        normalized = [('dbname' if k == 'database' else k, v) for k, v in query]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(normalized), parts.fragment))
    except Exception:
        return url



configured_db = os.getenv('DATABASE_PATH', 'data.db')
if not DATABASE_URL and os.path.dirname(configured_db):
    try:
        os.makedirs(os.path.dirname(configured_db), exist_ok=True)
        DB = configured_db
    except OSError:
        DB = 'data.db'
else:
    DB = configured_db


def raw_con():
    if DATABASE_URL:
        return psycopg.connect(normalize_database_url(DATABASE_URL), row_factory=dict_row)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def execute(c, sql, params=()):
    if DATABASE_URL:
        sql = sql.replace('?', '%s')
    return c.execute(sql, params)


def init():
    global DB_READY
    if DB_READY:
        return
    c = raw_con()
    if DATABASE_URL:
        execute(c, '''CREATE TABLE IF NOT EXISTS companies(
            id BIGSERIAL PRIMARY KEY, slug TEXT UNIQUE, name TEXT, agent_name TEXT,
            presentation TEXT, description TEXT, instagram TEXT, whatsapp TEXT,
            phone TEXT, email TEXT)''')
        execute(c, '''CREATE TABLE IF NOT EXISTS knowledge(
            id BIGSERIAL PRIMARY KEY, company_id BIGINT REFERENCES companies(id) ON DELETE CASCADE,
            filename TEXT, content TEXT)''')
    else:
        execute(c, 'CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,agent_name TEXT,presentation TEXT,description TEXT,instagram TEXT,whatsapp TEXT,phone TEXT,email TEXT)')
        execute(c, 'CREATE TABLE IF NOT EXISTS knowledge(id INTEGER PRIMARY KEY,company_id INTEGER,filename TEXT,content TEXT)')
    c.commit()
    c.close()
    DB_READY = True


def con():
    init()
    return raw_con()


def auth():
    return session.get('admin')


@app.route('/healthz')
def healthz():
    return jsonify(ok=True)


@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == os.getenv('ADMIN_PASSWORD', 'admin123'):
            session['admin'] = 1
            return redirect('/admin')
        return render_template('login.html', error='Senha inválida')
    return render_template('login.html')


@app.get('/admin/logout')
def logout():
    session.clear()
    return redirect('/admin/login')


@app.get('/')
def home():
    return redirect('/admin/login')


@app.get('/admin')
def admin():
    if not auth():
        return redirect('/admin/login')
    c = con()
    rows = execute(c, 'select * from companies order by id desc').fetchall()
    c.close()
    return render_template('dashboard.html', companies=rows)


@app.route('/admin/company', methods=['GET', 'POST'])
def company():
    if not auth():
        return redirect('/admin/login')
    if request.method == 'GET':
        return redirect('/admin')
    f = request.form
    s = f.get('slug', '').strip().lower().replace(' ', '-')
    if not s or not f.get('name', '').strip():
        return 'Nome e slug são obrigatórios.', 400
    c = con()
    try:
        execute(c, 'insert into companies(slug,name,agent_name,presentation,description,instagram,whatsapp,phone,email) values(?,?,?,?,?,?,?,?,?)',
                (s, f.get('name'), f.get('agent_name') or 'Assistente', f.get('presentation', ''), f.get('description', ''), f.get('instagram', ''), f.get('whatsapp', ''), f.get('phone', ''), f.get('email', '')))
        c.commit()
    except Exception as e:
        c.rollback()
        c.close()
        if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
            return 'Este slug já está sendo usado. Escolha outro.', 400
        raise
    c.close()
    return redirect('/admin')


@app.route('/admin/company/<int:i>/edit', methods=['GET', 'POST'])
def edit_company(i):
    if not auth():
        return redirect('/admin/login')
    c = con()
    co = execute(c, 'select * from companies where id=?', (i,)).fetchone()
    if not co:
        c.close()
        return 'Empresa não encontrada', 404
    if request.method == 'POST':
        f = request.form
        s = f.get('slug', '').strip().lower().replace(' ', '-')
        if not s or not f.get('name', '').strip():
            c.close()
            return 'Nome e slug são obrigatórios.', 400
        try:
            execute(c, 'update companies set slug=?,name=?,agent_name=?,presentation=?,description=?,instagram=?,whatsapp=?,phone=?,email=? where id=?',
                    (s, f.get('name'), f.get('agent_name') or 'Assistente', f.get('presentation', ''), f.get('description', ''), f.get('instagram', ''), f.get('whatsapp', ''), f.get('phone', ''), f.get('email', ''), i))
            c.commit()
        except Exception as e:
            c.rollback()
            c.close()
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                return 'Este slug já está sendo usado por outra empresa. Escolha outro.', 400
            raise
        c.close()
        return redirect('/admin')
    c.close()
    return render_template('edit_company.html', company=co)


@app.post('/admin/company/<int:i>/knowledge')
def knowledge(i):
    if not auth():
        return redirect('/admin/login')
    x = request.files.get('file')
    if x:
        c = con()
        execute(c, 'insert into knowledge(company_id,filename,content) values(?,?,?)', (i, x.filename, x.read().decode('utf8', 'replace')))
        c.commit()
        c.close()
    return redirect('/admin')


@app.get('/c/<slug>')
def public(slug):
    c = con()
    co = execute(c, 'select * from companies where slug=?', (slug,)).fetchone()
    c.close()
    if not co:
        return 'Empresa não encontrada', 404
    return render_template('chat.html', company=co)


@app.post('/api/chat/<slug>')
def chat(slug):
    c = con()
    co = execute(c, 'select * from companies where slug=?', (slug,)).fetchone()
    docs = execute(c, 'select filename,content from knowledge where company_id=?', (co['id'],)).fetchall() if co else []
    c.close()
    if not co:
        return jsonify(error='Empresa não encontrada'), 404
    m = (request.json or {}).get('message', '').strip()
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        return jsonify(reply='O atendimento por IA ainda não foi configurado. Fale com a equipe da empresa.')
    ctx = '\n'.join([
        f"Empresa: {co['name']}", f"Agente: {co['agent_name']}",
        f"Apresentação: {co['presentation']}", f"Descrição: {co['description']}",
        f"Instagram: {co['instagram']}", f"WhatsApp: {co['whatsapp']}",
        f"Telefone: {co['phone']}", f"E-mail: {co['email']}"
    ] + [f"Arquivo {d['filename']}: {d['content']}" for d in docs])
    sys = f'Você é {co["agent_name"]}, assistente comercial de {co["name"]}. Responda em português, naturalmente e somente com base no contexto. Nunca invente preços, promoções ou disponibilidade. Para orçamento, encaminhe para a equipe. Para promoções, indique o Instagram. Se for assunto fora da empresa, diga que não possui essa informação e ofereça contato humano.\n{ctx}'
    try:
        r = OpenAI(api_key=key).chat.completions.create(model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'), messages=[{'role': 'system', 'content': sys}, {'role': 'user', 'content': m}])
        ans = r.choices[0].message.content
    except Exception:
        ans = 'Não consegui concluir o atendimento agora. Fale diretamente com a equipe da empresa.'
    return jsonify(reply=ans)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '10000')))
